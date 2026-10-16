# Review and ticket videos (factory/videos.py): renders a validated scene script with a Remotion composition (src/) and, when the
# factory did not send the voice-over, speaks it with Piper. Run with NO network, a read-only root and no capabilities: it reads
# /in (read-only) and writes /out/review.mp4 and /out/timings.json. Build with this directory as context:
#   docker build -t factory-video -f sandbox/video/Dockerfile sandbox/video
# Needs network at build time only. package-lock.json pins the npm packages; the voice is pinned to a commit and checked by SHA-256.
#
# Licences: Remotion is free for individuals and companies of up to three people; larger companies need a Remotion company licence
# (https://www.remotion.dev/license). Piper is MIT; the voice is en_US-ljspeech-medium, trained on LJSpeech (public domain).
FROM docker.io/library/node:22-slim
ENV HOME=/tmp TZ=UTC LANG=C.UTF-8 NPM_CONFIG_UPDATE_NOTIFIER=false REMOTION_DISABLE_TELEMETRY=1
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl python3 python3-venv \
      libnss3 libdbus-1-3 libatk1.0-0 libgbm1 libasound2 libxrandr2 libxkbcommon0 libxfixes3 libxcomposite1 libxdamage1 \
      libatk-bridge2.0-0 libpango-1.0-0 libcairo2 libcups2 fonts-dejavu-core fonts-noto-core \
 && rm -rf /var/lib/apt/lists/*
# Piper and its voice
ARG VOICE=https://huggingface.co/rhasspy/piper-voices/resolve/c10ece1aade47bb51c153c893d14e5bf8e5b7117/en/en_US/ljspeech/medium/en_US-ljspeech-medium
RUN python3 -m venv /opt/piper && /opt/piper/bin/pip install --no-cache-dir piper-tts==1.8.0 \
 && mkdir -p /opt/voices && curl -fsSL -o /opt/voices/en_US-ljspeech-medium.onnx "$VOICE.onnx" \
 && curl -fsSL -o /opt/voices/en_US-ljspeech-medium.onnx.json "$VOICE.onnx.json" \
 && printf '%s  %s\n' 6f52a751e2349abe7a76735eb09dc1875298c77ea2342ffd2fef79ff81b87f22 /opt/voices/en_US-ljspeech-medium.onnx \
      141d612cc0a95ed7efc1ca936b845c2364967f2e9217c5dbfcf69fc4d6c65860 /opt/voices/en_US-ljspeech-medium.onnx.json | sha256sum -c -
# Remotion, its browser, and the composition bundled once
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev && npx --no-install remotion browser ensure && rm -rf /tmp/.npm /root/.npm
COPY src ./src
COPY public ./public
COPY bundle.mjs render.mjs ./
RUN node bundle.mjs && chmod -R a+rX /app /opt/piper /opt/voices
USER 1000:1000
# Remotion finds the browser it installed under ./node_modules/.remotion
WORKDIR /app
ENTRYPOINT ["node", "/app/render.mjs"]
