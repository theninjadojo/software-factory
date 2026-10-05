# Orchestrator + egress proxy image (one image, two commands). The sandbox image the agents run in is in sandbox/.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*
# Only the container CLI is needed: sandboxes are started on the engine reachable through DOCKER_HOST.
COPY --from=docker:27-cli /usr/local/bin/docker /usr/local/bin/docker
RUN useradd -m -u 1000 factory
WORKDIR /app
COPY factory ./factory
COPY tests ./tests
COPY worker ./worker
COPY scripts ./scripts
COPY deploy ./deploy
COPY sandbox/android ./sandbox/android
COPY .github/workflows ./.github/workflows
COPY config.example.toml VERSION ./
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HOME=/tmp
USER 1000:1000
CMD ["python3", "-m", "factory.main", "--config", "/etc/factory/config.toml"]
