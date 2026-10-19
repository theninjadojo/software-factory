// Step screenshots for a demo video: turn each Playwright trace into one PNG per step of the test. docs/videos.md
//
//   node trace-steps.mjs <test-results folder> <out folder> <name prefix>
//
// Run by playwright-screens.sh in a project folder after a run with tracing on (FACTORY_STEPS=1). Lives on the WORKER, outside the
// checkout. For every trace.zip under the results folder it reads the test's own steps (test.trace: Navigate, Fill, Click, ... in the
// test body, not its hooks or fixtures) and the page's screencast frames, and keeps, for each step, the last frame taken while that step
// ran (the run uses slowMo so the page has settled by then). A step with no frame of its own changed nothing visible and is left out,
// as is a frame the same as the one before. Each kept frame (a JPEG) is turned into a PNG with the project's own Playwright Chromium.
// A click, press, check, select or navigation also keeps a few of the frames taken while it ran, before that last one (its clip: a
// menu opening, a page loading), so the video plays what really happened instead of fading one picture into the next.
//
// Writes <out>/<name>.png and, when FACTORY_RESULTS_DIR is set, appends to it:
//   shots.tsv   <trace.zip path> TAB <name>.png         the worker links the PNG to the test whose trace it is
//   steps.tsv   <name>.png TAB <step number> TAB <label> TAB <motion>
//               the label is the action and its target, never a typed value; the motion (JSON, may be empty) is what a video animates:
//               {"act": click | fill | type | press | check | select | other, "x", "y": the pointer (click), "box": [x, y, w, h] where a
//               typed value appeared (fill, type: the pixels that changed from the step before, inside the field when the trace
//               says where it is), "field": [x, y, w, h] the element the step acted on (from the trace in Playwright 1.63 and
//               later, else found in the picture before the step: elementAt), "key": the key a press
//               pressed (named keys only, see keyName), "url": the page's path while it is known (after a navigation, until a click,
//               press, check or select may have left it)}, in the PNG's pixels, and for a step
//               with a clip "clip": [[ms after the step began, <frame name>], ...] and "end": ms after it began of the step's own frame
//   a clip's frames are <out>/zz-<name>-fN.png: named to sort after every step, so a worker's byte budget drops frames before steps
import {createHash} from 'node:crypto';
import {appendFileSync, readdirSync, readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {join, relative, resolve} from 'node:path';
import {inflateRawSync} from 'node:zlib';

const MAX_STEPS = 20;                         // per test
const MAX_TOTAL = 120;                        // per run
const MAX_ENTRY = 20_000_000;
const MAX_CLIP = 6;                           // in-between frames per step
const MAX_CLIP_TOTAL = 150;                   // per run (with MAX_TOTAL steps, under the worker's 300 artifacts)
const MAX_CLIP_MS = 10_000;

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

// A pressed key as a video may show it ("Enter", "Control+A"): a named key, or a letter or digit only with a modifier, so a value
// typed one key at a time is never shown; null for anything else.
export function keyName(k) {
  const named = 'Enter|Tab|Escape|Backspace|Delete|Space|ArrowUp|ArrowDown|ArrowLeft|ArrowRight|Home|End|PageUp|PageDown|F[1-9]|F1[0-2]';
  const re = new RegExp(`^(?:(?:(?:Control|Meta|Shift|Alt|ControlOrMeta)\\+){1,3}(?:${named}|[A-Za-z0-9])|(?:(?:Control|Meta|Shift|Alt|ControlOrMeta)\\+){0,3}(?:${named}))$`);
  return typeof k === 'string' && re.test(k) ? k : null;
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

// The frames of a clip: those taken from the step's start until its own frame, each different from the one before it (and from the
// picture before the step), at most MAX_CLIP spread evenly over the time and always ending with the last; [{at, frame}], at in ms.
export function clip(entries, frames, start, own, before) {
  const size = String(jpegSize(entries[own.file]));
  let last = before ? createHash('sha1').update(before).digest('hex') : '';
  const got = [];
  for (const x of frames) {
    if (x.timestamp < start || x.timestamp >= own.timestamp || x.timestamp - start > MAX_CLIP_MS) continue;
    const digest = createHash('sha1').update(entries[x.file]).digest('hex');
    if (digest === last || String(jpegSize(entries[x.file])) !== size) continue;
    last = digest;
    got.push({at: Math.round(x.timestamp - start), frame: entries[x.file]});
  }
  if (got.length <= MAX_CLIP) return got;
  return Array.from({length: MAX_CLIP}, (_, i) => got[Math.round(((i + 1) * got.length) / MAX_CLIP) - 1]);
}

// [{n, label, act, point, field, frame, before, clip, end}] for one trace: the test body's steps that have a frame of their own, in order.
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
  // event links to the test step (stepId; from Playwright 1.6x the call id is the test step's own). The point is in the page's CSS
  // pixels; the viewport says how those map onto a frame.
  const ctx = Object.keys(entries).filter((k) => k.endsWith('.trace') && k !== 'test.trace').flatMap((k) => lines(entries[k]));
  const stepOf = new Map(ctx.filter((e) => e.type === 'before').map((e) => [e.callId, e.stepId || e.callId]));
  const points = new Map(ctx.filter((e) => e.type === 'input' && e.point && stepOf.has(e.callId)).map((e) => [stepOf.get(e.callId), e.point]));
  const fields = new Map(ctx.filter((e) => e.type === 'input' && e.box && stepOf.has(e.callId)).map((e) => [stepOf.get(e.callId), e.box]));
  const view = ctx.find((e) => e.type === 'context-options' && e.options?.viewport)?.options.viewport;
  // A frame's picture: `file` in older traces, `sha1` under resources/ in newer ones (Playwright 1.4x and later).
  const image = (e) => typeof e.file === 'string' ? e.file : typeof e.sha1 === 'string' ? `resources/${e.sha1}` : '';
  const frames = ctx.filter((e) => e.type === 'screencast-frame' && entries[image(e)])
    .map((e) => ({...e, file: image(e)})).sort((a, b) => a.timestamp - b.timestamp);
  // A step's own frame is the last one before the next step starts (the last step's: before the test body's last check ends), so
  // what it set off and the page settled into after it (a page loading, a "Welcome back") is its screen, not the step after it's.
  const bodyEnd = Math.max(...events.filter((e) => e.type === 'before' && inBody(e) && ends.has(e.callId)).map((e) => ends.get(e.callId)));
  const out = [];
  let last = '', before = null, url = null;
  for (const [i, a] of acts.entries()) {
    const next = acts[i + 1]?.startTime;
    const f = frames.filter((x) => x.timestamp >= a.startTime && (next === undefined ? x.timestamp <= bodyEnd : x.timestamp < next)).pop();
    const kind = act(a);
    const where = label(a).match(/^Navigate (\S+)$/);
    url = where ? where[1] : kind === 'fill' || kind === 'type' || kind === 'other' ? url : null;   // a click may have left the page
    if (!f || out.length >= MAX_STEPS) continue;
    const digest = createHash('sha1').update(entries[f.file]).digest('hex');
    if (digest === last) continue;
    last = digest;
    const [fw, fh] = jpegSize(entries[f.file]) || [f.width, f.height];
    const p = points.get(a.callId), sx = view?.width ? fw / view.width : 1, sy = view?.height ? fh / view.height : 1;
    const point = p && Number.isFinite(p.x) && Number.isFinite(p.y) ? [Math.round(p.x * sx), Math.round(p.y * sy)] : null;
    const b = fields.get(a.callId);         // the element the step acted on, when the trace says (newer Playwright)
    const field = b && [b.x, b.y, b.width, b.height].every((v) => Number.isFinite(v) && v >= 0) && b.width > 0 && b.height > 0
      ? [Math.round(b.x * sx), Math.round(b.y * sy), Math.round(b.width * sx), Math.round(b.height * sy)] : null;
    const took = Math.round(f.timestamp - a.startTime);
    const moving = before && kind !== 'fill' && kind !== 'type' && took <= MAX_CLIP_MS;      // a typed value is animated from its box instead
    const k = kind === 'press' ? keyName(a.params?.key) : null;
    out.push({n: out.length + 1, label: label(a), act: kind, point, field, key: k, url, frame: entries[f.file], before, width: f.width, height: f.height,
              clip: moving ? clip(entries, frames, a.startTime, f, before) : [], end: took});
    before = entries[f.file];
  }
  return out;
}

// [x, y, w, h] of the field a value was typed into, from the pixels that differ between the frame before and after (JPEGs, worked out
// in the browser on a canvas); null when nothing changed or a large part of the page did (a new page, not a value in one field).
// The changed pixels are grouped into patches that touch (in 4-pixel cells); the field left behind only loses its focus ring, a thin
// outline, so the patch kept is the one with the most changed pixels inside its own outline: the typed text.
// With `within` (the field's own box) only the pixels inside it count, so a focus ring leaving another field is not mistaken for it.
export async function changed(page, a, b, within = null) {
  return page.evaluate(async ([a, b, within]) => {
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
      if (within && (px < within[0] + 4 || py < within[1] + 4 || px >= within[0] + within[2] - 4 || py >= within[1] + within[3] - 4)) continue;
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
  }, [`data:image/jpeg;base64,${a.toString('base64')}`, `data:image/jpeg;base64,${b.toString('base64')}`, within]);
}

// [x, y, w, h] of the element at `point` in a picture (RGBA `p`, W x H), for traces that do not say where the element a step acted on
// is (Playwright before 1.63 records only the click point): the region of the element's own colour that the point is in. Its colour is
// the commonest one around the point (a label's letters are fewer pixels than the button under them); the region is every pixel of
// about that colour joined to it, so the letters inside are skipped and its edge or border stops it. When that region runs out to the
// edge of the area searched (an item drawn flat on the page, a menu row, an outlined button: the colour is the page's), the element is
// what is drawn on the page there instead: the icon and words around the point, joined across small gaps (inkAt). Null when neither
// gives something the size of an element.
export function elementAt(p, W, H, point) {
  const [x, y] = point.map(Math.round);
  if (!(x >= 0 && y >= 0 && x < W && y < H)) return null;
  const R = 8, bin = (o) => ((p[o] >> 4) << 8) | ((p[o + 1] >> 4) << 4) | (p[o + 2] >> 4), count = new Map();
  for (let j = Math.max(0, y - R); j <= Math.min(H - 1, y + R); j++) {
    for (let i = Math.max(0, x - R); i <= Math.min(W - 1, x + R); i++) { const k = bin((j * W + i) * 4); count.set(k, (count.get(k) || 0) + 1); }
  }
  const mode = [...count].reduce((m, e) => (e[1] > m[1] ? e : m))[0];
  const seed = [0, 0, 0], todo = [];
  let n = 0;
  for (let j = Math.max(0, y - R); j <= Math.min(H - 1, y + R); j++) {
    for (let i = Math.max(0, x - R); i <= Math.min(W - 1, x + R); i++) {
      const o = (j * W + i) * 4;
      if (bin(o) === mode) { seed[0] += p[o]; seed[1] += p[o + 1]; seed[2] += p[o + 2]; n++; todo.push(j * W + i); }
    }
  }
  seed.forEach((v, c) => { seed[c] = v / n; });
  const X0 = Math.max(0, x - Math.round(W * 0.45)), X1 = Math.min(W - 1, x + Math.round(W * 0.45));
  const Y0 = Math.max(0, y - Math.round(H * 0.2)), Y1 = Math.min(H - 1, y + Math.round(H * 0.2));
  const like = (k) => { const o = k * 4; return Math.abs(p[o] - seed[0]) + Math.abs(p[o + 1] - seed[1]) + Math.abs(p[o + 2] - seed[2]) <= 48; };
  const seen = new Uint8Array(W * H);
  let x0 = x, y0 = y, x1 = x, y1 = y;
  for (const k of todo) seen[k] = 1;
  while (todo.length) {
    const k = todo.pop(), i = k % W, j = (k - i) / W;
    if (i <= X0 || i >= X1 || j <= Y0 || j >= Y1) return inkAt(p, W, H, [x, y], seed, [X0, Y0, X1, Y1]);    // it ran out: the page's colour
    x0 = Math.min(x0, i); y0 = Math.min(y0, j); x1 = Math.max(x1, i); y1 = Math.max(y1, j);
    for (const m of [k - 1, k + 1, k - W, k + W]) if (!seen[m] && like(m)) { seen[m] = 1; todo.push(m); }
  }
  const w = x1 - x0 + 1, h = y1 - y0 + 1;
  return w < 12 || h < 10 ? null : [x0, y0, w, h];

  // What is drawn on the background `bg` around the point: 4-pixel cells holding any pixel unlike it, grouped across gaps of up to
  // 16 pixels sideways (between an icon and its words, and the words) and 4 up and down (the lines of one label); the group nearest
  // the point, within 32 pixels of it (a button's middle may be empty), stretched to take in the point. Null when there is none, or it runs out of the area searched
  // or is not the size of an element.
  function inkAt(p, W, H, [x, y], bg, [X0, Y0, X1, Y1]) {
    const S = 4, CW = Math.floor((X1 - X0 + 1) / S), CH = Math.floor((Y1 - Y0 + 1) / S), ink = new Uint8Array(CW * CH);
    for (let cy = 0; cy < CH; cy++) {
      for (let cx = 0; cx < CW; cx++) {
        for (let j = 0; j < S && !ink[cy * CW + cx]; j++) {
          for (let i = 0; i < S; i++) {
            const o = ((Y0 + cy * S + j) * W + X0 + cx * S + i) * 4;
            if (Math.abs(p[o] - bg[0]) + Math.abs(p[o + 1] - bg[1]) + Math.abs(p[o + 2] - bg[2]) > 96) { ink[cy * CW + cx] = 1; break; }
          }
        }
      }
    }
    const px = Math.floor((x - X0) / S), py = Math.floor((y - Y0) / S);
    let start = -1, best = 1e9;
    for (let cy = Math.max(0, py - 8); cy <= Math.min(CH - 1, py + 8); cy++) {
      for (let cx = Math.max(0, px - 8); cx <= Math.min(CW - 1, px + 8); cx++) {
        const d = Math.hypot(cx - px, cy - py);
        if (ink[cy * CW + cx] && d < best) { best = d; start = cy * CW + cx; }
      }
    }
    if (start < 0) return null;
    const seen = new Uint8Array(CW * CH), todo = [start];
    let a0 = CW, b0 = CH, a1 = -1, b1 = -1;
    seen[start] = 1;
    while (todo.length) {
      const k = todo.pop(), cx = k % CW, cy = (k - cx) / CW;
      if (cx === 0 || cy === 0 || cx === CW - 1 || cy === CH - 1) return null;
      a0 = Math.min(a0, cx); b0 = Math.min(b0, cy); a1 = Math.max(a1, cx); b1 = Math.max(b1, cy);
      for (let dy = -1; dy <= 1; dy++) {
        for (let dx = -4; dx <= 4; dx++) {
          const nx = cx + dx, ny = cy + dy, n = ny * CW + nx;
          if (nx >= 0 && ny >= 0 && nx < CW && ny < CH && ink[n] && !seen[n]) { seen[n] = 1; todo.push(n); }
        }
      }
    }
    const l = Math.min(X0 + a0 * S, x - 6), t = Math.min(Y0 + b0 * S, y - 6);              // the point is on it too
    const w = Math.max(X0 + (a1 + 1) * S, x + 6) - l, h = Math.max(Y0 + (b1 + 1) * S, y + 6) - t;
    return w < 12 || h < 10 || w > W * 0.6 || h > H * 0.3 ? null : [l, t, w, h];
  }
}

// elementAt on a JPEG, decoded on a canvas in the recipe's browser page.
export async function element(page, jpeg, point) {
  return page.evaluate(async ([src, img, point]) => {
    const i = await new Promise((ok, bad) => { const m = new Image(); m.onload = () => ok(m); m.onerror = bad; m.src = img; });
    const c = document.createElement('canvas');
    c.width = i.width; c.height = i.height;
    const g = c.getContext('2d', {willReadFrequently: true});
    g.drawImage(i, 0, 0);
    return new Function(`return (${src})`)()(g.getImageData(0, 0, i.width, i.height).data, i.width, i.height, point);
  }, [elementAt.toString(), `data:image/jpeg;base64,${jpeg.toString('base64')}`, point]);
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
  let frames = 0;
  try {
    const page = await browser.newPage();
    const png = async (jpeg, name) => {
      await page.setContent(`<body style="margin:0"><img style="display:block" src="data:image/jpeg;base64,${jpeg.toString('base64')}"></body>`);
      await page.locator('img').screenshot({path: join(out, `${name}.png`)});
    };
    for (const s of all) {
      await page.setViewportSize({width: s.width || 1280, height: s.height || 720});
      await png(s.frame, s.name);
      const typed = s.act === 'fill' || s.act === 'type';
      const box = typed && s.before ? await changed(page, s.before, s.frame, s.field) : null;
      // where the element is, from the picture before the step when the trace does not say: its spotlight fits it, not the pointer
      const seed = s.point || (box && [box[0] + box[2] / 2, box[1] + box[3] / 2]);
      const field = s.field || (s.before && seed ? await element(page, s.before, seed).catch(() => null) : null);
      const m = {act: s.act, ...(s.point ? {x: s.point[0], y: s.point[1]} : {}), ...(box ? {box} : {}), ...(field ? {field} : {}),
                 ...(s.key ? {key: s.key} : {}), ...(s.url ? {url: s.url} : {})};
      const kept = s.clip.slice(0, Math.max(0, MAX_CLIP_TOTAL - frames));
      if (kept.length) {
        m.clip = [];
        for (const [i, c] of kept.entries()) {
          const name = `zz-${s.name}-f${i + 1}`;
          await png(c.frame, name);
          m.clip.push([c.at, name]);
        }
        m.end = s.end;
        frames += kept.length;
      }
      if (results) {
        appendFileSync(join(results, 'shots.tsv'), `${s.trace}\t${s.name}.png\n`);
        appendFileSync(join(results, 'steps.tsv'), `${s.name}.png\t${s.n}\t${s.label}\t${JSON.stringify(m)}\n`);
      }
    }
  } finally {
    await browser.close();
  }
  console.log(`[trace-steps] ${all.length} step screenshot(s) and ${frames} in-between frame(s) kept`);
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main(process.argv.slice(2)).catch((e) => { console.error(`[trace-steps] FAILED: ${e.message}`); process.exit(1); });
}
