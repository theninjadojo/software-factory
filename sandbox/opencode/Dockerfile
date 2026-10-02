# EXPERIMENTAL: OpenCode CLI sandbox (OpenRouter and OpenCode Zen). Build with the sandbox/ directory as the context:
#   docker build -t factory-agent-opencode -f sandbox/opencode/Dockerfile sandbox
# The command line used by the factory is in config (harnesses.opencode-*.command); it has not been verified end to end.
# The CLI version is pinned: check the flags below against it before bumping.
FROM docker.io/library/node:22-slim
ARG OPENCODE_VERSION=1.0.0
RUN apt-get update && apt-get install -y --no-install-recommends git socat curl ca-certificates \
 && rm -rf /var/lib/apt/lists/* && npm install -g opencode-ai@${OPENCODE_VERSION}
# Non-interactive, unattended config baked into the image (never from ticket text): allow edits and shell inside the
# sandbox, no autoupdate or sharing, and no remote model catalogue fetch (so models.dev need not be reachable).
ENV OPENCODE_CONFIG_CONTENT='{"permission":{"edit":"allow","bash":"allow"},"autoupdate":false,"share":"disabled"}' \
    OPENCODE_DISABLE_MODELS_FETCH=1 \
    OPENCODE_DISABLE_AUTOUPDATE=1
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 755 /usr/local/bin/entrypoint.sh
USER 1000:1000
WORKDIR /work
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
