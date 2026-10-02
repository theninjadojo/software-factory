FROM docker.io/library/node:22-slim
RUN apt-get update && apt-get install -y --no-install-recommends git socat curl ca-certificates \
 && rm -rf /var/lib/apt/lists/* && npm install -g @anthropic-ai/claude-code
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 755 /usr/local/bin/entrypoint.sh
USER 1000:1000
WORKDIR /work
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
