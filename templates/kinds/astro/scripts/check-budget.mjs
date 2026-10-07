// Performance budget for the built site. Run after `npm run build`; fails when dist/ goes over a limit.
// Sizes of HTML, CSS and JS are gzipped (what a browser downloads); images are raw.
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { extname, join, relative } from 'node:path';
import { gzipSync } from 'node:zlib';

const KB = 1024;
const BUDGET = {
  page: 30 * KB, // each HTML page
  css: 30 * KB, // all CSS
  js: 50 * KB, // all JavaScript (a content site should ship little or none)
  image: 200 * KB, // each image
};
const IMAGES = new Set(['.jpg', '.jpeg', '.png', '.webp', '.avif', '.gif', '.svg']);

const dist = 'dist';
const files = [];
const walk = (dir) => {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) walk(path);
    else files.push(path);
  }
};
walk(dist);

const gz = (path) => gzipSync(readFileSync(path)).length;
const failures = [];
const totals = { css: 0, js: 0 };

for (const file of files) {
  const ext = extname(file);
  const name = relative(dist, file);
  if (ext === '.html') {
    const size = gz(file);
    if (size > BUDGET.page) failures.push(`${name}: ${size} B gzipped > ${BUDGET.page} B`);
  } else if (ext === '.css') totals.css += gz(file);
  else if (ext === '.js' || ext === '.mjs') totals.js += gz(file);
  else if (IMAGES.has(ext)) {
    const size = statSync(file).size;
    if (size > BUDGET.image) failures.push(`${name}: ${size} B > ${BUDGET.image} B`);
  }
}
for (const kind of ['css', 'js']) {
  if (totals[kind] > BUDGET[kind]) failures.push(`all ${kind}: ${totals[kind]} B gzipped > ${BUDGET[kind]} B`);
}

console.log(`Budget: ${files.length} files, css ${totals.css} B, js ${totals.js} B (gzipped)`);
if (failures.length) {
  console.error(`Over budget:\n  ${failures.join('\n  ')}`);
  process.exit(1);
}
