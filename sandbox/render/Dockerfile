# Renders the factory's validated design canvases (.dc.html) to PNG previews. Run with NO network, a read-only root and no
# capabilities by factory/render.py: it only reads /in (read-only) and writes /out. Build with the sandbox/ directory as context:
#   docker build -t factory-render -f sandbox/render/Dockerfile sandbox/render
FROM docker.io/library/debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends chromium fonts-liberation fonts-dejavu-core fonts-noto-core \
      python3 python3-pil ca-certificates \
 && rm -rf /var/lib/apt/lists/*
COPY render.py /usr/local/bin/render.py
RUN chmod 755 /usr/local/bin/render.py
ENV HOME=/tmp
USER 1000:1000
WORKDIR /tmp
ENTRYPOINT ["python3", "/usr/local/bin/render.py"]
