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
//   steps.tsv   <name>.png TAB <step number> TAB <label> the label is the action and its target, never a typed value
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

// [{n, label, frame}] for one trace: the test body's steps that have a frame of their own, in order.
export function steps(entries) {
  const events = lines(entries['test.trace']);
  const byId = new Map(events.filter((e) => e.type === 'before').map((e) => [e.callId, e]));
  const ends = new Map(events.filter((e) => e.type === 'after').map((e) => [e.callId, e.endTime]));
  const inBody = (e) => {                                   // only test.step groups between the step and the test body
    for (let p = e.parentId; p; p = byId.get(p)?.parentId) if (byId.get(p)?.method !== 'test.step') return false;
    return true;
  };
  const acts = events.filter((e) => e.type === 'before' && e.method === 'pw:api' && inBody(e) && ends.has(e.callId));
  // A frame's picture: `file` in older traces, `sha1` under resources/ in newer ones (Playwright 1.4x and later).
  const image = (e) => typeof e.file === 'string' ? e.file : typeof e.sha1 === 'string' ? `resources/${e.sha1}` : '';
  const frames = Object.keys(entries).filter((k) => k.endsWith('.trace') && k !== 'test.trace')
    .flatMap((k) => lines(entries[k])).filter((e) => e.type === 'screencast-frame' && entries[image(e)])
    .map((e) => ({...e, file: image(e)})).sort((a, b) => a.timestamp - b.timestamp);
  const out = [];
  let last = '';
  for (const a of acts) {
    const end = ends.get(a.callId);
    const f = frames.filter((x) => x.timestamp >= a.startTime && x.timestamp <= end).pop();
    if (!f || out.length >= MAX_STEPS) continue;
    const digest = createHash('sha1').update(entries[f.file]).digest('hex');
    if (digest === last) continue;
    last = digest;
    out.push({n: out.length + 1, label: label(a), frame: entries[f.file], width: f.width, height: f.height});
  }
  return out;
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
      if (results) {
        appendFileSync(join(results, 'shots.tsv'), `${s.trace}\t${s.name}.png\n`);
        appendFileSync(join(results, 'steps.tsv'), `${s.name}.png\t${s.n}\t${s.label}\n`);
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
