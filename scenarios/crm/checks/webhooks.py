"""Checks introduced by the `webhooks` phase: signed, retried deliveries in order per contact.

The checks run a receiver on this machine; the app reaches it at `CHECKS_CALLBACK_HOST` (default
`host.docker.internal`, which Docker Desktop forwards to the host's loopback interface). On Linux, set
`CHECKS_CALLBACK_BIND=0.0.0.0` so the container can reach it through the host gateway.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

import httpx

from harness.suite import Suite, get_id, listing, mk, ok, rejected, status
from scenarios.crm.checks.helpers import fresh_agency, person

WAIT = 45  # seconds for all deliveries; the spec allows the first retry after up to 10 s
SLOW = 8   # seconds the slow receiver takes to answer


class Receiver(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        pass

    def body(self) -> bytes:
        if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
            data = b""
            while size := int(self.rfile.readline().strip() or b"0", 16):
                data += self.rfile.read(size)
                self.rfile.readline()
            self.rfile.readline()
            return data
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def do_POST(self) -> None:
        raw, log = self.body(), self.server.log  # type: ignore[attr-defined]
        try:
            event = json.loads(raw)
        except ValueError:
            event = None
        with log["lock"]:
            fail = self.path.startswith("/flaky") and not log["failed"]
            log["failed"] |= fail
            log["calls"].append({"at": time.monotonic(), "path": self.path, "raw": raw, "event": event,
                                 "signature": self.headers.get("X-Signature"), "ok": not fail})
        if self.path.startswith("/slow"):
            time.sleep(SLOW)
        self.send_response(500 if fail else 200)
        self.send_header("Content-Length", "0")
        self.end_headers()


def start_receiver() -> tuple[ThreadingHTTPServer, str, dict]:
    srv = ThreadingHTTPServer((os.environ.get("CHECKS_CALLBACK_BIND", "127.0.0.1"), 0), Receiver)
    srv.daemon_threads = True
    srv.log = {"lock": threading.Lock(), "calls": [], "failed": False}  # type: ignore[attr-defined]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    host = os.environ.get("CHECKS_CALLBACK_HOST", "host.docker.internal")
    return srv, f"http://{host}:{srv.server_address[1]}", srv.log  # type: ignore[attr-defined]


Step = tuple[str, Callable[[dict], bool]]


def first_accepted(calls: list[dict], path: str, steps: list[Step]) -> list[dict | None]:
    """For each expected step, the first accepted delivery to `path` that matches it (each delivery used once)."""
    out: list[dict | None] = []
    used: set[int] = set()
    for kind, pred in steps:
        hit = next((x for x in calls if x["ok"] and x["path"] == path and id(x) not in used
                    and isinstance(x["event"], dict) and x["event"].get("type") == kind
                    and isinstance(x["event"].get("data"), dict) and pred(x["event"])), None)
        if hit:
            used.add(id(hit))
        out.append(hit)
    return out


def v14(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("v14")
    st = s.state
    srv, url, log = start_receiver()
    ag, ag2 = fresh_agency(c, "hooks-main"), fresh_agency(c, "hooks-flaky")
    subs: list[str] = []

    def subscribe(agency: str, path: str, events: list[str]) -> dict:
        sub = ok(c.post(f"/agencies/{agency}/subscriptions", json={"url": url + path, "events": events}), "subscribe")
        assert get_id(sub) and isinstance(sub.get("secret"), str) and sub["secret"], f"subscription {str(sub)[:200]}"
        subs.append(get_id(sub))  # type: ignore[arg-type]
        return sub

    def validation():
        mk(c, "/contacts", person(ag, "Hook", "Owner"), "contact")  # claims the agency id for this run
        for what, body in (("ftp url", {"url": "ftp://example.com/x", "events": ["contact.created"]}),
                           ("not a url", {"url": "hooks", "events": ["contact.created"]}),
                           ("no events", {"url": url + "/x", "events": []}),
                           ("unknown event", {"url": url + "/x", "events": ["contact.exploded"]})):
            rejected(c.post(f"/agencies/{ag}/subscriptions", json=body), what)
        assert not listing(c, f"/agencies/{ag}/subscriptions"), "a rejected subscription was stored"
    s.check("rejects_invalid_subscriptions", validation)

    def changes():
        main = subscribe(ag, "/main", ["contact.created", "contact.updated", "contact.deleted", "deal.created",
                                       "deal.updated", "deal.stage_changed"])
        subscribe(ag, "/slow", ["contact.updated", "deal.updated"])
        gone = subscribe(ag, "/gone", ["contact.created"])
        status(c.delete(f"/subscriptions/{get_id(gone)}"), 204, "DELETE subscription")
        assert get_id(gone) not in {get_id(x) for x in listing(c, f"/agencies/{ag}/subscriptions")}, "deleted sub listed"
        timings = []

        def timed(r: httpx.Response, what: str) -> Any:
            timings.append((r.elapsed.total_seconds(), what))
            return ok(r, what) if r.status_code != 204 else None

        x = get_id(timed(c.post("/contacts", json=person(ag, "Xavi", "Hook")), "create contact"))
        timed(c.patch(f"/contacts/{x}", json={"title": "Buyer"}), "PATCH contact")
        deal = get_id(timed(c.post("/deals", json={"agencyId": ag, "contactId": x, "title": "Loft", "value": 10,
                                                   "stage": "lead"}), "create deal"))
        timed(c.patch(f"/deals/{deal}", json={"stage": "proposal"}), "PATCH deal")
        timed(c.patch(f"/deals/{deal}", json={"value": 20}), "PATCH deal again")
        timed(c.delete(f"/contacts/{x}"), "DELETE contact")
        st.update(main=main, x=x, deal=deal, timings=timings)
        subscribe(ag2, "/flaky", ["contact.created", "contact.updated"])
        y = get_id(ok(c.post("/contacts", json=person(ag2, "Yara", "Flaky")), "create flaky contact"))
        for title in ("v1", "v2", "v3"):
            ok(c.patch(f"/contacts/{y}", json={"title": title}), "PATCH flaky contact")
        st["y"] = y
    s.check("make_changes", changes)

    def main_steps() -> list[Step]:
        x, deal = s.need("x"), s.need("deal")
        return [("contact.created", lambda e: e["data"].get("id") == x and e["data"].get("title") is None),
                ("contact.updated", lambda e: e["data"].get("id") == x and e["data"].get("title") == "Buyer"),
                ("deal.created", lambda e: get_id(e["data"]) == deal and e["data"].get("stage") == "lead"),
                ("deal.stage_changed", lambda e: get_id(e["data"]) == deal and e["data"].get("stage") == "proposal"
                 and e.get("previousStage") == "lead"),
                ("deal.updated", lambda e: get_id(e["data"]) == deal and e["data"].get("value") == 20),
                ("contact.deleted", lambda e: get_id(e["data"]) == x)]

    def flaky_steps() -> list[Step]:
        y = s.need("y")
        return [("contact.created", lambda e: get_id(e["data"]) == y)] + [
            ("contact.updated", lambda e, t=t: get_id(e["data"]) == y and e["data"].get("title") == t)
            for t in ("v1", "v2", "v3")]

    def wait_all():
        steps = {"/main": main_steps(), "/flaky": flaky_steps()}
        deadline = time.monotonic() + WAIT
        while time.monotonic() < deadline:
            with log["lock"]:
                calls = list(log["calls"])
            if all(all(first_accepted(calls, p, st_)) for p, st_ in steps.items()):
                break
            time.sleep(0.5)
        st["calls"] = calls
        for p, st_ in steps.items():
            missing = [k for (k, _), hit in zip(st_, first_accepted(calls, p, st_)) if not hit]
            assert not missing, f"{p}: not delivered within {WAIT} s: {missing} ({len(calls)} deliveries received)"
    s.check("events_delivered", wait_all)

    def content():
        calls, main = s.need("calls"), s.need("main")
        hits = first_accepted(calls, "/main", main_steps())
        for hit in hits:
            e = hit["event"]  # type: ignore[index]
            assert e.get("id") and e.get("occurredAt") and e.get("agencyId") == ag, f"event {str(e)[:200]}"
            want = hmac.new(main["secret"].encode(), hit["raw"], hashlib.sha256).hexdigest()  # type: ignore[index]
            got = (hit["signature"] or "").lower().removeprefix("sha256=")  # type: ignore[index]
            assert hmac.compare_digest(got, want), f"{e.get('type')}: X-Signature {hit['signature']!r} is not the HMAC"  # type: ignore[index]
        assert len({h["event"]["id"] for h in hits}) == len(hits), "events share an id"  # type: ignore[index]
    s.check("event_content_and_signature", content)

    def order():
        hits = first_accepted(s.need("calls"), "/main", main_steps())
        got = [h["event"]["type"] for h in sorted(hits, key=lambda h: h["at"])]  # type: ignore[index]
        assert got == [k for k, _ in main_steps()], f"main: events arrived out of order: {got}"
    s.check("in_order_per_contact", order)

    def retried():
        calls, y = s.need("calls"), s.need("y")
        flaky = [x for x in calls if x["path"] == "/flaky" and isinstance(x["event"], dict)]
        failed = [x for x in flaky if not x["ok"]]
        assert failed, "the receiver never failed a delivery"
        again = [x for x in flaky if x["ok"] and x["event"].get("id") == failed[0]["event"].get("id")]
        assert again, "the failed delivery was not retried with the same event id"
        hits = first_accepted(calls, "/flaky", flaky_steps())
        accepted = [h["at"] for h in hits]  # type: ignore[index]
        assert accepted == sorted(accepted), "events of the contact accepted out of order"
        for x in flaky:
            k = next((i for i, h in enumerate(hits) if h and h["event"].get("id") == x["event"].get("id")), None)
            if k and x["at"] < accepted[k - 1]:
                raise AssertionError(f"{x['event'].get('type')} delivered before the earlier event of contact {y} was accepted")
    s.check("retried_and_held_back_until_accepted", retried)

    def fast():
        slow = [(t, what) for t, what in s.need("timings") if t > 2]
        assert not slow, f"requests waited for deliveries: {slow}"
    s.check("api_not_slowed_by_receivers", fast)

    def unsubscribed():
        gone = [x for x in s.need("calls") if x["path"] == "/gone"]
        assert not gone, f"{len(gone)} deliveries after the subscription was deleted"
    s.check("deleted_subscription_gets_nothing", unsubscribed)

    for sid in subs:  # stop retries to this receiver once it is gone
        c.delete(f"/subscriptions/{sid}")
    srv.shutdown()
    return s.results
