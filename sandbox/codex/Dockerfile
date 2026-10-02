# EXPERIMENTAL: OpenAI Codex CLI sandbox. Build with the sandbox/ directory as the context:
#   docker build -t factory-agent-codex -f sandbox/codex/Dockerfile sandbox
# The command line used by the factory is in config (harnesses.codex.command); it has not been verified end to end.
FROM docker.io/library/node:22-slim
RUN apt-get update && apt-get install -y --no-install-recommends git socat curl ca-certificates \
 && rm -rf /var/lib/apt/lists/* && npm install -g @openai/codex
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 755 /usr/local/bin/entrypoint.sh
USER 1000:1000
WORKDIR /work
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
