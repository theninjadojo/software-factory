// Renders each shot of /spec/spec.json from the repo copy at /in, served on 127.0.0.1, into /out/<id>.png.
// The container has no network. Page code is untrusted: this file only writes PNGs, nothing else is trusted from the page.
const http = require("http"), fs = require("fs"), path = require("path");
const { chromium } = require("playwright");

const ROOT = fs.realpathSync("/in");
const TYPES = { ".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".mjs": "text/javascript", ".json": "application/json",
  ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp", ".woff": "font/woff", ".woff2": "font/woff2" };

const server = http.createServer((req, res) => {
  try {
    let p = path.join(ROOT, decodeURIComponent(new URL(req.url, "http://x").pathname));
    if (fs.existsSync(p) && fs.statSync(p).isDirectory()) p = path.join(p, "index.html");
    const real = fs.realpathSync(p);
    if (real !== ROOT && !real.startsWith(ROOT + path.sep)) throw new Error("outside");   // symlinks may not leave the repo copy
    res.writeHead(200, { "content-type": TYPES[path.extname(real)] || "application/octet-stream" });
    fs.createReadStream(real).pipe(res);
  } catch (e) { res.writeHead(404); res.end(); }
});

(async () => {
  const spec = JSON.parse(fs.readFileSync("/spec/spec.json", "utf8"));
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch({ args: ["--no-sandbox", "--font-render-hinting=none", "--disable-gpu"] });
  let failed = 0;
  for (const s of spec.shots) {
    const ctx = await browser.newContext({ viewport: { width: s.width, height: s.height }, deviceScaleFactor: 1, locale: "en-US",
      timezoneId: "UTC", reducedMotion: "reduce", serviceWorkers: "block" });
    await ctx.route((u) => new URL(u).origin !== origin, (r) => r.abort());       // belt and braces: nothing leaves 127.0.0.1
    try {
      const page = await ctx.newPage();
      await page.goto(`${origin}/${s.path}`, { waitUntil: "networkidle", timeout: 30000 });
      if (s.wait_for) await page.waitForSelector(s.wait_for, { timeout: 15000 });
      await page.screenshot({ path: `/out/${s.id}.png`, fullPage: true, animations: "disabled", caret: "hide",
        mask: s.mask.map((m) => page.locator(m)), maskColor: "#ff00ff" });
    } catch (e) { failed++; console.error(`${s.id}: ${e.message}`); }
    await ctx.close();
  }
  await browser.close();
  server.close();
  process.exit(failed ? 1 : 0);
})();
