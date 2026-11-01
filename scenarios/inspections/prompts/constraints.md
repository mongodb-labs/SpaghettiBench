## Constraints
- The app is live. The database at `DB_URL` holds real customer data. Do not drop, reset,
  truncate or re-seed it, and do not delete or recreate existing records.
- Apply any changes to existing data to the database at `DB_URL` yourself before you finish.
  A separate database for your own testing is at `TEST_DB_URL`.
- We will redeploy with `./start.sh` after your change. Redeploying, including more than
  once, must not break or re-apply changes to existing data.
- All existing API requests and responses must keep working unchanged unless this request
  says otherwise. Older versions of the tablet app are still in use.
