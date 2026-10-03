// Compares /actual/<id>.png with /base/<id>.png (same size, checked by the orchestrator) for each shot of /spec/spec.json.
// Writes /out/<id>-diff.png and /out/verdict.json {"results":[{"id","diff_pixels"}]}. It sees only PNGs: never page code.
const fs = require("fs");
const { PNG } = require("pngjs");
const pixelmatch = require("pixelmatch").default || require("pixelmatch");

const spec = JSON.parse(fs.readFileSync("/spec/spec.json", "utf8"));
const results = [];
for (const { id } of spec.shots) {
  const a = PNG.sync.read(fs.readFileSync(`/actual/${id}.png`)), b = PNG.sync.read(fs.readFileSync(`/base/${id}.png`));
  const diff = new PNG({ width: a.width, height: a.height });
  const n = a.width === b.width && a.height === b.height ? pixelmatch(a.data, b.data, diff.data, a.width, a.height, { threshold: spec.threshold }) : a.width * a.height;
  fs.writeFileSync(`/out/${id}-diff.png`, PNG.sync.write(diff));
  results.push({ id, diff_pixels: n });
}
fs.writeFileSync("/out/verdict.json", JSON.stringify({ results }));
