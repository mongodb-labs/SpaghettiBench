FROM debian:bookworm

ARG CLAUDE_CODE_VERSION=latest

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl git build-essential \
    python3 python3-pip python3-venv golang \
 && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && rm -rf /var/lib/apt/lists/*

RUN npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}

# Owned by dev so the named volume inherits the ownership on first mount
RUN useradd -m -s /bin/bash dev \
 && mkdir -p /workspace \
 && chown -R dev:dev /workspace

# --dangerously-skip-permissions refuses to run as root
USER dev
RUN git config --global user.name dev \
 && git config --global user.email dev@example.com \
 && git config --global init.defaultBranch main

WORKDIR /workspace
