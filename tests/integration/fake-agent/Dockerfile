FROM docker.io/library/node:22-slim
RUN apt-get update && apt-get install -y --no-install-recommends git socat curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
COPY fake-agent /usr/local/bin/fake-agent
COPY mockup.html /usr/local/share/mockup.html
RUN chmod 755 /usr/local/bin/entrypoint.sh /usr/local/bin/fake-agent
USER 1000:1000
WORKDIR /work
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
