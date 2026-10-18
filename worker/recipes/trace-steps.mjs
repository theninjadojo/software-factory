// Step screenshots for a demo video: turn each Playwright trace into one PNG per step of the test. docs/videos.md
//
//   node trace-steps.mjs <test-results folder> <out folder> <name prefix>
//
// Run by playwright-screens.sh in a project folder after a run with tracing on (FACTORY_STEPS=1). Lives on the WORKER, outside the
// checkout. For every trace.zip under the results folder it reads the test's own steps (test.trace: Navigate, Fill, Click, ... in the
// test body, not its hooks or fixtures) and the page's screencast frames, and keeps, for each step, the last frame taken while that step
// ran (the run uses slowMo so the page has settled by then). A step with no frame of its own changed nothing visible and is left out,
// as is a frame the same as the one before. Each kept frame (a JPEG) is turned into a PNG with the project's own Playwright Chromium.
//
// Writes <out>/<name>.png and, when FACTORY_RESULTS_DIR is set, appends to it:
//   shots.tsv   <trace.zip path> TAB <name>.png         the worker links the PNG to the test whose trace it is
//   steps.tsv   <name>.png TAB <step number> TAB <label> TAB <motion>
//               the label is the action and its target, never a typed value; the motion (JSON, may be empty) is what a video animates:
//               {"act": click | fill | type | press | check | select | other, "x", "y": the pointer (click), "box": [x, y, w, h] where a
//               typed value appeared (fill, type: the pixels that changed from the step before)}, in the PNG's pixels
import {createHash} from 'node:crypto';
import {appendFileSync, readdirSync, readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {join, relative, resolve} from 'node:path';
import {inflateRawSync} from 'node:zlib';

const MAX_STEPS = 20;                         // per test
const MAX_TOTAL = 120;                        // per run
const MAX_ENTRY = 20_000_000;

// The entries of a zip file: {name: Buffer}, only those `want` accepts (stored or deflated; anything else is skipped).
export function unzip(buf, want) {
  let end = -1;
  for (let i = buf.length - 22; i >= Math.max(0, buf.length - 65557); i--) {
    if (buf.readUInt32LE(i) === 0x06054b50) { end = i; break; }
  }
  if (end < 0) throw new Error('not a zip file');
  const count = buf.readUInt16LE(end + 10);
  let p = buf.readUInt32LE(end + 16);
  const out = {};
  for (let n = 0; n < count && p + 46 <= buf.length && buf.readUInt32LE(p) === 0x02014b50; n++) {
    const method = buf.readUInt16LE(p + 10), size = buf.readUInt32LE(p + 20), full = buf.readUInt32LE(p + 24);
    const nameLen = buf.readUInt16LE(p + 28), extra = buf.readUInt16LE(p + 30), comment = buf.readUInt16LE(p + 32);
    const local = buf.readUInt32LE(p + 42);
    const name = buf.toString('utf8', p + 46, p + 46 + nameLen);
    p += 46 + nameLen + extra + comment;
    if (!want(name) || full > MAX_ENTRY || local + 30 > buf.length) continue;
    const start = local + 30 + buf.readUInt16LE(local + 26) + buf.readUInt16LE(local + 28);
    const data = buf.subarray(start, start + size);
    if (method === 0) out[name] = data;
    else if (method === 8) out[name] = inflateRawSync(data, {maxOutputLength: MAX_ENTRY});
  }
  return out;
}

const lines = (buf) => (buf ? buf.toString('utf8').split('\n') : []).flatMap((l) => {
  try { return l ? [JSON.parse(l)] : []; } catch { return []; }
});

// A step's label: the action and what it acted on, never the value it typed ('Fill "secret"' -> 'Fill'), at most 160 characters.
export function label(ev) {
  let verb = String(ev.title || '').replace(/"(?:[^"\\]|\\.)*"/g, '').replace(/\s+/g, ' ').trim();
  let target = String(ev.subtitle || '');
  if (verb === 'Navigate' || verb === 'Navigate to') {      // newer traces: 'Navigate to "/login"', the URL in params
    verb = 'Navigate';
    target = String(target || ev.params?.url || '');
    target = /^data:/i.test(target) ? '' : target.replace(/^[a-z]+:\/\/[^/]*/i, '').replace(/[?#].*$/, '') || '/';
  }
  return `${verb} ${target}`.replace(/[\x00-\x1f\x7f]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 160);
}

// [width, height] of a JPEG, from its frame header; null when it has none. A trace's frames can be smaller than the page.
export function jpegSize(buf) {
  for (let i = 2; i + 9 < buf.length && buf[i] === 0xff;) {
    const m = buf[i + 1];
    if (m >= 0xc0 && m <= 0xcf && ![0xc4, 0xc8, 0xcc].includes(m)) return [buf.readUInt16BE(i + 7), buf.readUInt16BE(i + 5)];
    i += 2 + buf.readUInt16BE(i + 2);
  }
  return null;
}

// What a step did, from its title: click, fill, type, press, check, select or other.
export function act(ev) {
  const verb = String(ev.title || '').split(/\s/)[0].toLowerCase();
  if (['click', 'dblclick', 'tap'].includes(verb)) return 'click';
  if (['check', 'uncheck', 'setchecked'].includes(verb)) return 'check';
  if (verb === 'select' || verb === 'selectoption') return 'select';
  if (['fill', 'type', 'press'].includes(verb)) return verb;
  return 'other';
}

// [{n, label, act, point, frame, before}] for one trace: the test body's steps that have a frame of their own, in order.
export function steps(entries) {
  const events = lines(entries['test.trace']);
  const byId = new Map(events.filter((e) => e.type === 'before').map((e) => [e.callId, e]));
  const ends = new Map(events.filter((e) => e.type === 'after').map((e) => [e.callId, e.endTime]));
  const inBody = (e) => {                                   // only test.step groups between the step and the test body
    for (let p = e.parentId; p; p = byId.get(p)?.parentId) if (byId.get(p)?.method !== 'test.step') return false;
    return true;
  };
  const acts = events.filter((e) => e.type === 'before' && e.method === 'pw:api' && inBody(e) && ends.has(e.callId));
  // Where the pointer went: the context trace's input events carry a point for a click, keyed by its own call id, which its `before`
  // event links to the test step (stepId). The point is in the page's CSS pixels; the viewport says how those map onto a frame.
  const ctx = Object.keys(entries).filter((k) => k.endsWith('.trace') && k !== 'test.trace').flatMap((k) => lines(entries[k]));
  const stepOf = new Map(ctx.filter((e) => e.type === 'before' && e.stepId).map((e) => [e.callId, e.stepId]));
  const points = new Map(ctx.filter((e) => e.type === 'input' && e.point && stepOf.has(e.callId)).map((e) => [stepOf.get(e.callId), e.point]));
  const view = ctx.find((e) => e.type === 'context-options' && e.options?.viewport)?.options.viewport;
  // A frame's picture: `file` in older traces, `sha1` under resources/ in newer ones (Playwright 1.4x and later).
  const image = (e) => typeof e.file === 'string' ? e.file : typeof e.sha1 === 'string' ? `resources/${e.sha1}` : '';
  const frames = ctx.filter((e) => e.type === 'screencast-frame' && entries[image(e)])
    .map((e) => ({...e, file: image(e)})).sort((a, b) => a.timestamp - b.timestamp);
  const out = [];
  let last = '', before = null;
  for (const a of acts) {
    const end = ends.get(a.callId);
    const f = frames.filter((x) => x.timestamp >= a.startTime && x.timestamp <= end).pop();
    if (!f || out.length >= MAX_STEPS) continue;
    const digest = createHash('sha1').update(entries[f.file]).digest('hex');
    if (digest === last) continue;
    last = digest;
    const [fw, fh] = jpegSize(entries[f.file]) || [f.width, f.height];
    const p = points.get(a.callId), sx = view?.width ? fw / view.width : 1, sy = view?.height ? fh / view.height : 1;
    const point = p && Number.isFinite(p.x) && Number.isFinite(p.y) ? [Math.round(p.x * sx), Math.round(p.y * sy)] : null;
    out.push({n: out.length + 1, label: label(a), act: act(a), point, frame: entries[f.file], before, width: f.width, height: f.height});
    before = entries[f.file];
  }
  return out;
}

// [x, y, w, h] of the field a value was typed into, from the pixels that differ between the frame before and after (JPEGs, worked out
// in the browser on a canvas); null when nothing changed or a large part of the page did (a new page, not a value in one field).
// The changed pixels are grouped into patches that touch (in 4-pixel cells); the field left behind only loses its focus ring, a thin
// outline, so the patch kept is the one with the most changed pixels inside its own outline: the typed text.
export async function changed(page, a, b) {
  return page.evaluate(async ([a, b]) => {
    const load = (src) => new Promise((ok, bad) => { const i = new Image(); i.onload = () => ok(i); i.onerror = bad; i.src = src; });
    const [x, y] = await Promise.all([load(a), load(b)]);
    if (x.width !== y.width || x.height !== y.height) return null;
    const W = x.width, H = x.height, c = document.createElement('canvas');
    c.width = W; c.height = H;
    const g = c.getContext('2d', {willReadFrequently: true});
    g.drawImage(x, 0, 0);
    const p = g.getImageData(0, 0, W, H).data;
    g.drawImage(y, 0, 0);
    const q = g.getImageData(0, 0, W, H).data;
    const S = 4, CW = Math.ceil(W / S), CH = Math.ceil(H / S), cell = new Int32Array(CW * CH), on = [];
    for (let i = 0; i < p.length; i += 4) {
      if (Math.abs(p[i] - q[i]) + Math.abs(p[i + 1] - q[i + 1]) + Math.abs(p[i + 2] - q[i + 2]) < 90) continue;   // JPEG noise
      const k = i / 4, px = k % W, py = (k - px) / W;
      on.push(px, py);
      cell[Math.floor(py / S) * CW + Math.floor(px / S)]++;
    }
    if (!on.length || on.length / 2 > W * H / 4) return null;
    const group = new Int32Array(CW * CH).fill(-1), boxes = [];
    for (let start = 0; start < cell.length; start++) {
      if (!cell[start] || group[start] >= 0) continue;
      const bx = {x0: W, y0: H, x1: -1, y1: -1, inside: 0}, todo = [start];
      group[start] = boxes.length;
      while (todo.length) {
        const k = todo.pop(), cx = k % CW, cy = (k - cx) / CW;
        bx.x0 = Math.min(bx.x0, cx * S); bx.y0 = Math.min(bx.y0, cy * S);
        bx.x1 = Math.max(bx.x1, Math.min(W, cx * S + S) - 1); bx.y1 = Math.max(bx.y1, Math.min(H, cy * S + S) - 1);
        for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
          const nx = cx + dx, ny = cy + dy, n = ny * CW + nx;
          if (nx >= 0 && ny >= 0 && nx < CW && ny < CH && cell[n] && group[n] < 0) { group[n] = boxes.length; todo.push(n); }
        }
      }
      boxes.push(bx);
    }
    for (let i = 0; i < on.length; i += 2) {
      const bx = boxes[group[Math.floor(on[i + 1] / S) * CW + Math.floor(on[i] / S)]];
      if (on[i] > bx.x0 + 3 && on[i] < bx.x1 - 3 && on[i + 1] > bx.y0 + 3 && on[i + 1] < bx.y1 - 3) bx.inside++;
    }
    const best = boxes.reduce((m, bx) => (bx.inside > m.inside ? bx : m));
    const w = best.x1 - best.x0 + 1, h = best.y1 - best.y0 + 1;
    return !best.inside || w * h > W * H / 4 || h > H / 3 ? null : [best.x0, best.y0, w, h];
  }, [`data:image/jpeg;base64,${a.toString('base64')}`, `data:image/jpeg;base64,${b.toString('base64')}`]);
}

const slug = (s) => s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');

function traces(dir) {
  const found = [];
  const walk = (d) => {
    for (const e of readdirSync(d, {withFileTypes: true})) {
      if (e.isDirectory()) walk(join(d, e.name));
      else if (e.isFile() && e.name === 'trace.zip') found.push(join(d, e.name));
    }
  };
  try { walk(dir); } catch { /* no results folder: nothing ran */ }
  return found.sort();
}

async function main([dir, out, prefix = '']) {
  const results = process.env.FACTORY_RESULTS_DIR;
  const all = [];
  for (const t of traces(dir)) {
    let got;
    try {
      got = steps(unzip(readFileSync(t), (n) => n.endsWith('.trace') || n.startsWith('screencast/') || n.startsWith('resources/')));
    } catch (e) {
      console.error(`[trace-steps] could not read ${relative(dir, t)}: ${e.message}`);
      continue;
    }
    const base = prefix + slug(relative(dir, t).replace(/\/trace\.zip$/, ''));
    const sum = createHash('sha1').update(t).digest('hex').slice(0, 6);
    const head = base.length > 40 ? `${base.slice(0, 18)}-${base.slice(-21)}` : base;
    for (const s of got) if (all.length < MAX_TOTAL) all.push({...s, trace: resolve(t), name: `${head}-s${String(s.n).padStart(2, '0')}-${sum}`});
  }
  if (!all.length) return console.log('[trace-steps] no steps found');
  const req = createRequire(resolve('package.json'));
  const {chromium} = req('@playwright/test');
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    for (const s of all) {
      await page.setViewportSize({width: s.width || 1280, height: s.height || 720});
      await page.setContent(`<body style="margin:0"><img style="display:block" src="data:image/jpeg;base64,${s.frame.toString('base64')}"></body>`);
      await page.locator('img').screenshot({path: join(out, `${s.name}.png`)});
      const box = (s.act === 'fill' || s.act === 'type') && s.before ? await changed(page, s.before, s.frame) : null;
      const m = {act: s.act, ...(s.point ? {x: s.point[0], y: s.point[1]} : {}), ...(box ? {box} : {})};
      if (results) {
        appendFileSync(join(results, 'shots.tsv'), `${s.trace}\t${s.name}.png\n`);
        appendFileSync(join(results, 'steps.tsv'), `${s.name}.png\t${s.n}\t${s.label}\t${JSON.stringify(m)}\n`);
      }
    }
  } finally {
    await browser.close();
  }
  console.log(`[trace-steps] ${all.length} step screenshot(s) kept`);
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main(process.argv.slice(2)).catch((e) => { console.error(`[trace-steps] FAILED: ${e.message}`); process.exit(1); });
}
