// Makes one video from /in/script.json with the Remotion composition bundled into this image (src/). Run with NO network, a read-only
// root and no capabilities by factory/videos.py; it reads /in (read-only) and writes /out/review.mp4 and /out/timings.json only.
//
// /in/script.json  {width, height, fps, max_seconds, voice: "piper" | "files" | "none", scenes: [scene]}
//   scene          {kind: title | shot | code | points, say, title?, subtitle?, image?, lines?: [{t, text}], points?: [text], audio?}
//   a shot may say its picture's size: [w, h] (a portrait phone screen is laid out beside its title and words, Video.jsx)
//   a shot of a demo step may add what the step did, to animate it from the picture before:
//                  before (a file in /in/img), size: [w, h] of both pictures, act: click | fill | type | press | check | select | other,
//                  x, y (the pointer), box: [x, y, w, h] (where a value was typed), field: [x, y, w, h] (the field it went into), in
//                  the pictures' pixels,
//                  clip: [{at, image}] frames taken while the step ran (ms after it began, in order) with end: ms of `image`,
//                  chain: true when `before` is the scene before's picture (the video carries on from it, no fade)
// /in/img/*.png    pictures named by the scenes (shot)
// /in/audio/*.wav  the spoken text of each scene, when the factory made it (voice "files", e.g. ElevenLabs)
// With voice "piper" this container speaks each scene's `say` itself with the voice baked into the image.
// Exit codes: 0 made, 2 bad input, 3 longer than max_seconds, 1 anything else.
import {spawnSync} from 'node:child_process';
import {copyFileSync, cpSync, existsSync, lstatSync, mkdirSync, readFileSync, writeFileSync} from 'node:fs';
import {renderMedia, selectComposition} from '@remotion/renderer';

const IN = process.env.VIDEO_IN || '/in';
const OUT = process.env.VIDEO_OUT || '/out';
const SITE = '/tmp/site';
const NAME = /^[a-z0-9][a-z0-9-]{0,40}\.(png|wav)$/;
const KINDS = new Set(['title', 'shot', 'code', 'points']);
const ACTS = new Set(['click', 'fill', 'type', 'press', 'check', 'select', 'other']);
const PIPER = ['/opt/piper/bin/python', '-m', 'piper', '-m', '/opt/voices/en_US-ljspeech-medium.onnx'];

class Bad extends Error {}

const str = (v, n, what, need = false) => {
  if (v === undefined || v === null || v === '') {
    if (need) throw new Bad(`${what} is missing`);
    return '';
  }
  if (typeof v !== 'string' || v.length > n) throw new Bad(`${what} must be text of at most ${n} characters`);
  return v;
};

const file = (dir, v, what) => {
  if (typeof v !== 'string' || !NAME.test(v)) throw new Bad(`${what} is not a usable file name`);
  const p = `${IN}/${dir}/${v}`;
  if (!existsSync(p) || lstatSync(p).isSymbolicLink() || !lstatSync(p).isFile()) throw new Bad(`${what} ${v} is not in /in/${dir}`);
  return v;
};

const int = (v, lo, hi, what) => {
  if (!Number.isInteger(v) || v < lo || v > hi) throw new Bad(`${what} must be a whole number from ${lo} to ${hi}`);
  return v;
};

export function check(spec) {
  if (!spec || typeof spec !== 'object') throw new Bad('script.json is not an object');
  const out = {
    width: int(spec.width ?? 1280, 320, 1920, 'width'), height: int(spec.height ?? 720, 240, 1080, 'height'),
    fps: int(spec.fps ?? 30, 10, 60, 'fps'), max_seconds: int(spec.max_seconds ?? 120, 5, 900, 'max_seconds'),
    voice: spec.voice ?? 'piper',
  };
  if (!['piper', 'files', 'none'].includes(out.voice)) throw new Bad('voice must be piper, files or none');
  if (!Array.isArray(spec.scenes) || spec.scenes.length < 1 || spec.scenes.length > 40) throw new Bad('scenes must be 1 to 40 scenes');
  out.scenes = spec.scenes.map((s, i) => {
    const w = `scene ${i + 1}`;
    if (!s || !KINDS.has(s.kind)) throw new Bad(`${w}: kind must be title, shot, code or points`);
    const c = {kind: s.kind, say: str(s.say, 600, `${w} say`), title: str(s.title, 200, `${w} title`, s.kind !== 'shot'),
               subtitle: str(s.subtitle, 300, `${w} subtitle`)};
    if (s.kind === 'shot') c.image = file('img', s.image, `${w} image`);
    if (s.kind === 'shot' && (s.size !== undefined || s.before !== undefined)) {
      if (!Array.isArray(s.size) || s.size.length !== 2) throw new Bad(`${w}: size must be [width, height]`);
      c.size = s.size.map((v, j) => int(v, 1, 10000, `${w} size ${j + 1}`));
    }
    if (s.kind === 'shot' && s.before !== undefined) {
      c.before = file('img', s.before, `${w} before`);
      if (!ACTS.has(s.act)) throw new Bad(`${w}: act must be one of ${[...ACTS].join(', ')}`);
      c.act = s.act;
      if (s.x !== undefined || s.y !== undefined) [c.x, c.y] = [int(s.x, 0, c.size[0], `${w} x`), int(s.y, 0, c.size[1], `${w} y`)];
      for (const k of ['box', 'field']) {
        if (s[k] === undefined) continue;
        if (!Array.isArray(s[k]) || s[k].length !== 4) throw new Bad(`${w}: ${k} must be [x, y, width, height]`);
        c[k] = s[k].map((v, j) => int(v, 0, 10000, `${w} ${k} ${j + 1}`));
      }
      if (s.clip !== undefined) {
        if (!Array.isArray(s.clip) || s.clip.length < 1 || s.clip.length > 6) throw new Bad(`${w}: clip must be 1 to 6 frames`);
        c.clip = s.clip.map((x, j) => ({at: int(x?.at, 0, 10000, `${w} clip ${j + 1} at`), image: file('img', x?.image, `${w} clip ${j + 1}`)}));
        c.clip.forEach((x, j) => { if (j && x.at <= c.clip[j - 1].at) throw new Bad(`${w}: clip frames must be in time order`); });
        c.end = int(s.end, c.clip[c.clip.length - 1].at, 10000, `${w} end`);
      }
      if (s.chain !== undefined && typeof s.chain !== 'boolean') throw new Bad(`${w}: chain must be true or false`);
      if (s.chain) c.chain = true;
    }
    if (s.kind === 'code') {
      const lines = s.lines ?? [];
      if (!Array.isArray(lines) || lines.length > 60) throw new Bad(`${w}: lines must be a list of at most 60 lines`);
      c.lines = lines.map((l, j) => {
        if (!l || !['+', '-', '@', ' '].includes(l.t)) throw new Bad(`${w} line ${j + 1}: t must be +, -, @ or a space`);
        return {t: l.t, text: str(l.text, 200, `${w} line ${j + 1}`)};
      });
    }
    if (s.kind === 'points') {
      if (!Array.isArray(s.points) || s.points.length < 1 || s.points.length > 6) throw new Bad(`${w}: points must be 1 to 6 points`);
      c.points = s.points.map((p, j) => str(p, 160, `${w} point ${j + 1}`, true));
    }
    if (out.voice === 'files' && c.say) c.audio = file('audio', s.audio, `${w} audio`);
    return c;
  });
  return out;
}

// The length of a PCM WAV file in seconds, from its fmt and data chunks.
export function wavSeconds(buf) {
  if (buf.length < 44 || buf.toString('ascii', 0, 4) !== 'RIFF' || buf.toString('ascii', 8, 12) !== 'WAVE') throw new Error('not a WAV file');
  let i = 12, byteRate = 0;
  while (i + 8 <= buf.length) {
    const id = buf.toString('ascii', i, i + 4), n = buf.readUInt32LE(i + 4);
    if (id === 'fmt ') byteRate = buf.readUInt32LE(i + 16);
    if (id === 'data') {
      if (!byteRate) break;
      return Math.min(n, buf.length - i - 8) / byteRate;
    }
    i += 8 + n + (n % 2);
  }
  throw new Error('a WAV file without fmt or data');
}

function speak(text, dest) {
  const r = spawnSync(PIPER[0], [...PIPER.slice(1), '-f', dest], {input: text, encoding: 'utf8', timeout: 120000});
  if (r.status !== 0 || !existsSync(dest)) throw new Error(`piper failed: ${(r.stderr || '').slice(-300)}`);
}

async function main() {
  let spec;
  try {
    spec = check(JSON.parse(readFileSync(`${IN}/script.json`, 'utf8')));
  } catch (e) {
    console.error(`bad input: ${e.message}`);
    process.exit(2);
  }
  cpSync('/app/bundle', SITE, {recursive: true});
  mkdirSync(`${SITE}/public/img`, {recursive: true});
  mkdirSync(`${SITE}/public/audio`, {recursive: true});
  const scenes = [], seconds = [];
  for (const [i, s] of spec.scenes.entries()) {
    const sc = {...s};
    for (const f of [s.image, s.before, ...(s.clip || []).map((x) => x.image)]) if (f) copyFileSync(`${IN}/img/${f}`, `${SITE}/public/img/${f}`);
    let talk = 0;
    if (s.say && spec.voice !== 'none') {
      sc.audio = spec.voice === 'files' ? s.audio : `s${String(i + 1).padStart(2, '0')}.wav`;
      const dest = `${SITE}/public/audio/${sc.audio}`;
      if (spec.voice === 'files') copyFileSync(`${IN}/audio/${s.audio}`, dest);
      else speak(s.say, dest);
      talk = wavSeconds(readFileSync(dest));
    } else {
      delete sc.audio;
      talk = s.say ? s.say.split(/\s+/).length / 2.6 : 0;      // time to read the subtitle
    }
    const secs = Math.max(2.5, talk + 0.8, s.clip ? 2.6 + s.end / 1000 : 0);      // a clip plays in full after the pointer gets there
    sc.frames = Math.ceil(secs * spec.fps);
    seconds.push(sc.frames / spec.fps);
    scenes.push(sc);
  }
  const total = seconds.reduce((a, b) => a + b, 0);
  if (total > spec.max_seconds) {
    console.error(`the video would be ${total.toFixed(1)}s, longer than ${spec.max_seconds}s`);
    process.exit(3);
  }
  const inputProps = {scenes, width: spec.width, height: spec.height, fps: spec.fps};
  const composition = await selectComposition({serveUrl: SITE, id: 'video', inputProps});
  await renderMedia({composition, serveUrl: SITE, codec: 'h264', audioCodec: 'aac', outputLocation: '/tmp/review.mp4', inputProps,
                     concurrency: 2, logLevel: 'warn'});
  // moov before mdat, so the player can start before the whole file has arrived (and factory/videos.py mp4_ok accepts it)
  const ff = spawnSync('npx', ['--no-install', 'remotion', 'ffmpeg', '-v', 'error', '-y', '-i', '/tmp/review.mp4', '-c', 'copy', '-movflags', '+faststart',
                               `${OUT}/review.mp4`], {encoding: 'utf8', cwd: '/app'});
  if (ff.status !== 0) throw new Error(`ffmpeg faststart failed: ${(ff.stderr || '').slice(-300)}`);
  writeFileSync(`${OUT}/timings.json`, JSON.stringify({scenes: seconds}));
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((e) => {
    console.error(e && e.stack ? e.stack.slice(0, 2000) : String(e));
    process.exit(1);
  });
}
