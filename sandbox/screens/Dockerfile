# Screen verification (factory/screens.py): renders configured pages with a pinned Playwright Chromium and compares them with
# committed baselines. Two runs of this image, both with NO network, a read-only root and no capabilities: `shoot.js` (runs the
# repo's page code, writes PNGs) and `compare.js` (sees only PNGs). Build with this directory as context:
#   docker build -t factory-screens -f sandbox/screens/Dockerfile sandbox/screens
# Needs network at build time only. package-lock.json pins every version; build with `npm ci`.
FROM docker.io/library/node:22-slim
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/pw HOME=/tmp TZ=UTC LANG=C.UTF-8
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev && npx playwright install --with-deps chromium \
 && apt-get install -y --no-install-recommends fonts-liberation fonts-dejavu-core fonts-noto-core \
 && rm -rf /var/lib/apt/lists/* /root/.npm
COPY shoot.js compare.js ./
RUN chmod -R a+rX /app /opt/pw
USER 1000:1000
ENTRYPOINT ["node"]
