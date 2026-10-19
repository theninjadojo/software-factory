import {AbsoluteFill, Audio, Easing, Img, Sequence, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';

// Fixed templates. Every piece of text arrives as a prop and is rendered as React text (never as HTML), every picture and sound is
// a file render.mjs copied into the bundle's public folder under a name it chose. Nothing here fetches or runs anything else.
const C = {bg: '#12161a', panel: '#1a2026', line: '#2d363d', text: '#e8edf0', muted: '#a9b5bd', add: '#52c7a1', del: '#ff6b5e', hunk: '#8fa8ff', accent: '#e0a030'};
const SANS = "'DejaVu Sans', 'Noto Sans', sans-serif";
const MONO = "'DejaVu Sans Mono', monospace";

// In and out of black, unless the scene carries on from the one before (in) or into the one after (out).
const useFade = (frames, fadeIn = true, fadeOut = true) => {
  const f = useCurrentFrame();
  return interpolate(f, [0, 10, frames - 10, frames], [fadeIn ? 0 : 1, 1, 1, fadeOut ? 0 : 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
};

const useRise = (delay = 0) => {
  const f = useCurrentFrame();
  const {fps} = useVideoConfig();
  return spring({frame: f - delay, fps, config: {damping: 200}});
};

const Subtitle = ({text}) => (
  text ? (
    <div style={{position: 'absolute', left: 64, right: 64, bottom: 36, padding: '12px 20px', background: 'rgba(0,0,0,0.62)',
                 borderRadius: 8, color: C.text, fontFamily: SANS, fontSize: 24, lineHeight: 1.35, textAlign: 'center'}}>
      {text}
    </div>
  ) : null
);

const Title = ({s}) => {
  const r = useRise();
  const r2 = useRise(8);
  return (
    <AbsoluteFill style={{justifyContent: 'center', padding: '0 96px', fontFamily: SANS}}>
      <div style={{width: 72, height: 6, background: C.accent, marginBottom: 28, transform: `scaleX(${r})`, transformOrigin: 'left'}} />
      <div style={{color: C.text, fontSize: 60, fontWeight: 700, lineHeight: 1.15, opacity: r, transform: `translateY(${(1 - r) * 24}px)`}}>
        {s.title}
      </div>
      {s.subtitle ? <div style={{color: C.muted, fontSize: 30, marginTop: 20, opacity: r2}}>{s.subtitle}</div> : null}
    </AbsoluteFill>
  );
};

const Picture = ({s}) => {
  const f = useCurrentFrame();
  const zoom = interpolate(f, [0, s.frames], [1, 1.06]);
  return (
    <div style={{flex: 1, overflow: 'hidden', border: `1px solid ${C.line}`, borderRadius: 10, background: C.panel}}>
      <Img src={staticFile(`img/${s.image}`)} style={{width: '100%', height: '100%', objectFit: 'contain', transform: `scale(${zoom})`}} />
    </div>
  );
};

const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'};
const ease = (f, a, b) => interpolate(f, [a, b], [0, 1], {...clamp, easing: Easing.inOut(Easing.cubic)});

// The pointer: an arrow with its tip at (0, 0).
const Pointer = ({x, y, opacity, press}) => (
  <svg width="30" height="30" viewBox="0 0 24 24" style={{position: 'absolute', left: x, top: y, opacity, overflow: 'visible',
       transform: `scale(${press})`, transformOrigin: '0 0', filter: 'drop-shadow(0 2px 3px rgba(0,0,0,0.45))'}}>
    <path d="M1 1 L1 19 L6 14.5 L9.5 22 L13 20.5 L9.5 13 L16 13 Z" fill="#fff" stroke="#111" strokeWidth="1.4" strokeLinejoin="round" />
  </svg>
);

// Where a demo step's pointer goes, in its pictures' pixels: the start of a typed value, else the click; null when it has none.
const targetOf = (s) => (!s || !s.before ? null
  : (s.act === 'fill' || s.act === 'type') && s.box ? [s.box[0] + Math.min(14, s.box[2] / 4), s.box[1] + s.box[3] / 2]
  : s.x !== undefined ? [s.x, s.y] : null);

// Where the camera looks for a demo step, in its pictures' pixels: the pointer's target, else the middle of the element it acted on.
const focusOf = (s) => targetOf(s) || (s?.before && s.field ? [s.field[0] + s.field[2] / 2, s.field[1] + s.field[3] / 2] : null);

// The camera stays in between two steps of one flow whose targets are close (both in view at once when zoomed in): it pans from one
// to the other instead of pulling out and pushing back in.
const ZOOM = 1.5;
const near = (a, b, s) => Boolean(a && b) && Math.abs(a[0] - b[0]) < s.size[0] * 0.3 && Math.abs(a[1] - b[1]) < s.size[1] * 0.3;

const arc = Easing.bezier(0.35, 0, 0.15, 1);

// A pressed key as a keycap reads it: "Control+Shift+Z" -> "Ctrl + Shift + Z", "Enter" -> "⏎ Enter".
const GLYPH = {Enter: '⏎ Enter', Tab: '⇥ Tab', Escape: 'Esc', Backspace: '⌫', Delete: 'Del', ArrowUp: '↑', ArrowDown: '↓', ArrowLeft: '←',
               ArrowRight: '→', Space: 'Space'};
const keycap = (k) => k.split('+').map((p) => (p === 'ControlOrMeta' || p === 'Control' ? 'Ctrl' : p === 'Meta' ? '⌘' : GLYPH[p] || p)).join(' + ');

// The browser around a demo step's page: window buttons and the address bar, with the page's path when it is known.
const BAR = 34;
const Browser = ({url, width}) => (
  <div style={{position: 'absolute', left: 0, top: 0, width, height: BAR, background: '#dde1e5', borderBottom: '1px solid #c3c8cd',
               display: 'flex', alignItems: 'center', gap: 7, padding: '0 12px', boxSizing: 'border-box'}}>
    {['#ff5f57', '#febc2e', '#28c840'].map((c) => <div key={c} style={{width: 11, height: 11, borderRadius: 6, background: c, flex: 'none'}} />)}
    <div style={{marginLeft: 14, flex: 1, maxWidth: width * 0.6, height: 22, borderRadius: 11, background: '#fff', color: '#4a5359',
                 fontFamily: SANS, fontSize: 13, lineHeight: '22px', padding: '0 12px', overflow: 'hidden', whiteSpace: 'nowrap'}}>
      {url || ''}
    </div>
  </div>
);

// A demo step played out on the real pictures, like a screen recording, in a browser window. While the narration gets going (lead)
// the page waits; then the camera pushes in on what the step acts on, the page around it dims and a label names it, while the pointer
// glides in on a slight arc (from where the step before left it, when this scene carries on from that one). Then a click (a press
// and a ripple), a key (its keycap pops up) or a value typed into its field behind a caret, then the frames the run took while the
// step ran (a menu opening, a page loading; their waits sped up) and the camera easing back out to the whole page.
const Step = ({s, from, keep, look, hold}) => {
  const lead = s.lead || 0;
  const f = useCurrentFrame() - lead;
  const len = s.frames - lead;
  const {width, height, fps} = useVideoConfig();
  const [fw, fh] = s.size;
  const top = 40 + (s.title ? 52 : 0), aw = width - 128, ah = height - top - 120 - BAR;
  const k = Math.min(aw / fw, ah / fh), dw = fw * k, dh = fh * k;
  const ox = 64 + (aw - dw) / 2, oy = top + (ah - dh) / 2;
  const typing = (s.act === 'fill' || s.act === 'type') && s.box;
  const target = targetOf(s);
  const T = target && [target[0] * k, target[1] * k];
  const box = s.field && s.field.map((v) => v * k);
  const focus = T || (box && [box[0] + box[2] / 2, box[1] + box[3] / 2]);
  const F = from && [from[0] * k, from[1] * k];
  const start = T ? (F || [Math.min(dw - 20, T[0] + 160), Math.min(dh - 20, T[1] + 110)]) : F;
  const dist = T ? Math.hypot(T[0] - start[0], T[1] - start[1]) : 0;
  const arrive = T ? 6 + Math.round(Math.max(14, Math.min(26, dist / 25))) : focus ? 20 : 0;
  const reveal = typing ? Math.max(20, Math.min(75, len - arrive - 34)) : 0;
  const ms = (v) => Math.round((v * fps) / 1000);
  const click = arrive + 3;
  const clip = (s.clip || []).map((c) => ({...c, from: click + ms(c.at)}));
  const done = typing ? arrive + 4 + reveal : s.clip ? click + ms(s.end) : focus ? arrive + 2 : 12;

  // The camera: in on the target as the pointer travels, held through the step, back out once its result has been seen.
  const Z = focus ? ZOOM : 1;
  const L = look && [look[0] * k, look[1] * k];          // already in on the step before's target: pan from there
  // (with a clip, as it starts playing: the page it leads to is seen whole, not a corner of it)
  const out0 = s.clip ? click + 6 : Math.max(arrive + 8, Math.min(done + 18, len - 26));
  const p = focus ? (L ? 1 : ease(f, 2, arrive + 4)) * (hold ? 1 : 1 - ease(f, out0, out0 + 22)) : 0;
  const z = focus ? 1 + (Z - 1) * p : keep ? 1 : interpolate(f, [0, len], [1, 1.04]);
  const m0 = L ? ease(f, 2, arrive + 2) : 1;
  const o = focus && L ? [L[0] + (focus[0] - L[0]) * m0, L[1] + (focus[1] - L[1]) * m0] : focus || [dw / 2, dh / 2];
  const pan = (lo, hi, want) => Math.max(lo, Math.min(hi, want));
  const tx = pan(-(z - 1) * (dw - o[0]), (z - 1) * o[0], (dw / 2 - o[0]) * p * 0.6);
  const ty = pan(-(z - 1) * (dh - o[1]), (z - 1) * o[1], (dh / 2 - o[1]) * p * 0.6);

  let pointer = null;
  if (start) {
    const [ex, ey] = T || start;
    const dl = Math.hypot(ex - start[0], ey - start[1]) || 1;
    const c = [(start[0] + ex) / 2 + ((ey - start[1]) / dl) * dl * 0.18, (start[1] + ey) / 2 - ((ex - start[0]) / dl) * dl * 0.18];
    const m = T ? arc(interpolate(f, [6, arrive], [0, 1], clamp)) : 1;
    const x = (1 - m) ** 2 * start[0] + 2 * (1 - m) * m * c[0] + m ** 2 * ex;
    const y = (1 - m) ** 2 * start[1] + 2 * (1 - m) * m * c[1] + m ** 2 * ey;
    const opacity = F ? 1 : interpolate(f, [4, 10], [0, 1], clamp);
    const press = T ? interpolate(f, [arrive, arrive + 3, arrive + 7], [1, 0.82, 1], clamp) : 1;
    pointer = <Pointer x={x} y={y} opacity={opacity} press={press} />;
  }
  const ring = T ? interpolate(f, [arrive, arrive + 16], [0, 1], clamp) : 1;
  const glow = T ? interpolate(f, [arrive - 8, arrive, arrive + 10, arrive + 18], [0, 0.35, 0.35, 0], clamp) : 0;
  // The spotlight and its label: from just before the step acts until it has (a click or key: as it is pressed; typing: once typed).
  const off = typing ? done + 6 : click - 2;
  const lit = focus ? interpolate(f, [arrive - 14, arrive - 4], [0, 1], clamp) * (1 - interpolate(f, [off, off + 6], [0, 1], clamp)) : 0;
  const shown = clip.filter((c) => f >= c.from).pop();
  const after = s.clip ? (f >= done ? 1 : 0) : interpolate(f, [done, done + 6], [0, 1], clamp);
  let typed = null;
  if (typing) {
    const q = interpolate(f, [arrive + 4, arrive + 4 + reveal], [0, 1], clamp);
    const [bx, by, bw, bh] = s.box.map((v) => v * k);
    const [fx, fy, fW, fH] = box || [bx - 6, by - 5, bw + 12, bh + 10];
    const edge = bx + bw * q;
    const ring2 = interpolate(f, [arrive, arrive + 4, done + 8, done + 16], [0, 1, 1, 0], clamp);
    typed = (
      <>
        <Img src={staticFile(`img/${s.image}`)} style={{position: 'absolute', inset: 0, width: dw, height: dh,
             clipPath: `inset(${by - 2}px ${dw - edge}px ${dh - by - bh - 2}px ${bx - 2}px)`}} />
        <div style={{position: 'absolute', left: fx - 3, top: fy - 3, width: fW + 6, height: fH + 6, borderRadius: 8,
                     border: `2px solid ${C.accent}`, boxShadow: `0 0 0 4px rgba(224,160,48,0.18)`, opacity: ring2}} />
        {f >= arrive + 4 && f < done ? (
          <div style={{position: 'absolute', left: edge, top: by + bh * 0.18, width: 2, height: bh * 0.64, background: '#111',
                       opacity: Math.floor(f / 8) % 2 === 0 || q < 1 ? 1 : 0}} />
        ) : null}
      </>
    );
  }
  let spot = null;
  if (lit > 0 && (box || T)) {
    const [sx, sy, sw, sh] = box ? [box[0] - 6, box[1] - 6, box[2] + 12, box[3] + 12] : [T[0] - 30, T[1] - 22, 60, 44];
    const below = sy < 46 / z;
    spot = (
      <>
        <div style={{position: 'absolute', left: sx, top: sy, width: sw, height: sh, borderRadius: 8, opacity: lit,
                     boxShadow: '0 0 0 4000px rgba(12,16,20,0.38)'}} />
        {s.callout ? (
          <div style={{position: 'absolute', left: sx, top: below ? sy + sh + 8 / z : sy - 8 / z, opacity: lit,
                       transform: `translateY(${below ? 0 : -100}%)`, background: C.accent, color: '#1b1b1b', fontFamily: SANS,
                       fontWeight: 700, fontSize: 17 / z, lineHeight: 1.2, padding: `${5 / z}px ${10 / z}px`, borderRadius: 6 / z,
                       whiteSpace: 'nowrap', boxShadow: '0 4px 14px rgba(0,0,0,0.3)'}}>
            {s.callout}
          </div>
        ) : null}
      </>
    );
  }
  const pop = s.key ? spring({frame: f - arrive, fps, config: {damping: 12, stiffness: 180}}) : 0;
  const keyOut = s.key ? 1 - interpolate(f, [arrive + 40, arrive + 50], [0, 1], clamp) : 0;
  return (
    <AbsoluteFill style={{fontFamily: SANS}}>
      {s.title ? <div style={{position: 'absolute', left: 64, top: 40, right: 64, color: C.text, fontSize: 30, fontWeight: 700}}>{s.title}</div> : null}
      <div style={{position: 'absolute', left: ox, top: oy, width: dw, height: dh + BAR, overflow: 'hidden', border: `1px solid ${C.line}`,
                   borderRadius: 10, background: C.panel, boxShadow: '0 18px 50px rgba(0,0,0,0.45)'}}>
        <Browser url={s.url} width={dw} />
        <div style={{position: 'absolute', left: 0, top: BAR, width: dw, height: dh, overflow: 'hidden'}}>
          <div style={{position: 'absolute', inset: 0, transform: `translate(${tx}px, ${ty}px) scale(${z})`, transformOrigin: `${o[0]}px ${o[1]}px`}}>
            <Img src={staticFile(`img/${s.before}`)} style={{position: 'absolute', inset: 0, width: dw, height: dh}} />
            {shown ? <Img src={staticFile(`img/${shown.image}`)} style={{position: 'absolute', inset: 0, width: dw, height: dh}} /> : null}
            {typed}
            <Img src={staticFile(`img/${s.image}`)} style={{position: 'absolute', inset: 0, width: dw, height: dh, opacity: after}} />
            {spot}
            {T && glow > 0 ? (
              <div style={{position: 'absolute', left: T[0] - 34, top: T[1] - 34, width: 68, height: 68, borderRadius: '50%',
                           background: `radial-gradient(circle, rgba(224,160,48,0.9), rgba(224,160,48,0) 70%)`, opacity: glow}} />
            ) : null}
            {T && ring < 1 ? (
              <div style={{position: 'absolute', left: T[0] - 26 * ring, top: T[1] - 26 * ring, width: 52 * ring,
                           height: 52 * ring, borderRadius: '50%', border: `3px solid ${C.accent}`, opacity: 1 - ring}} />
            ) : null}
            {pointer}
          </div>
        </div>
        {s.key && f >= arrive ? (
          <div style={{position: 'absolute', left: dw / 2, bottom: 22, transform: `translateX(-50%) scale(${pop})`, opacity: keyOut,
                       background: '#f7f7f5', color: '#1b1b1b', border: '1px solid #bbb', borderBottomWidth: 4, borderRadius: 10,
                       padding: '10px 22px', fontFamily: SANS, fontSize: 26, fontWeight: 700, boxShadow: '0 8px 24px rgba(0,0,0,0.35)'}}>
            {keycap(s.key)}
          </div>
        ) : null}
      </div>
    </AbsoluteFill>
  );
};

const Shot = ({s, from, keep, look, hold}) => {
  if (s.before) return <Step s={s} from={from} keep={keep} look={look} hold={hold} />;
  return (
    <AbsoluteFill style={{padding: '40px 64px 120px', fontFamily: SANS}}>
      {s.title ? <div style={{color: C.text, fontSize: 30, fontWeight: 700, marginBottom: 16}}>{s.title}</div> : null}
      <Picture s={s} />
    </AbsoluteFill>
  );
};

const Code = ({s}) => {
  const f = useCurrentFrame();
  return (
    <AbsoluteFill style={{padding: '40px 64px 120px', fontFamily: SANS}}>
      <div style={{color: C.text, fontSize: 28, fontWeight: 700, marginBottom: 6, wordBreak: 'break-all'}}>{s.title}</div>
      {s.subtitle ? <div style={{color: C.muted, fontSize: 20, marginBottom: 14}}>{s.subtitle}</div> : null}
      <div style={{flex: 1, overflow: 'hidden', border: `1px solid ${C.line}`, borderRadius: 10, background: C.panel, padding: '14px 18px'}}>
        {s.lines.length === 0 ? (
          <div style={{color: C.muted, fontSize: 22, fontFamily: SANS}}>No text diff to show (a binary or very large file).</div>
        ) : null}
        {s.lines.map((l, i) => (
          <div key={i} style={{fontFamily: MONO, fontSize: 17, lineHeight: 1.4, whiteSpace: 'pre', overflow: 'hidden',
                               color: l.t === '+' ? C.add : l.t === '-' ? C.del : l.t === '@' ? C.hunk : C.text,
                               opacity: interpolate(f, [4 + i * 1.5, 10 + i * 1.5], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'})}}>
            {l.text || ' '}
          </div>
        ))}
      </div>
    </AbsoluteFill>
  );
};

const Point = ({text, i}) => {
  const r = useRise(8 + i * 12);
  return (
    <div style={{display: 'flex', gap: 18, marginBottom: 22, opacity: r, transform: `translateX(${(1 - r) * 30}px)`}}>
      <div style={{width: 12, height: 12, marginTop: 14, borderRadius: 6, background: C.accent, flex: 'none'}} />
      <div style={{color: C.text, fontSize: 32, lineHeight: 1.35}}>{text}</div>
    </div>
  );
};

const Points = ({s}) => (
  <AbsoluteFill style={{padding: '64px 96px 130px', fontFamily: SANS}}>
    <div style={{color: C.text, fontSize: 44, fontWeight: 700, marginBottom: 36}}>{s.title}</div>
    {s.points.map((p, i) => <Point key={i} text={p} i={i} />)}
  </AbsoluteFill>
);

const KINDS = {title: Title, shot: Shot, code: Code, points: Points};

// A scene; a demo step that carries on from the one before (chain) cuts straight in, keeping the pointer where that one left it.
const Scene = ({s, from, keep, look, hold}) => {
  const Body = KINDS[s.kind];
  return (
    <AbsoluteFill style={{background: C.bg, opacity: useFade(s.frames, !s.chain, !keep)}}>
      <Body s={s} from={from} keep={keep} look={look} hold={hold} />
      <Subtitle text={s.say} />
      {s.audio ? <Audio src={staticFile(`audio/${s.audio}`)} /> : null}
    </AbsoluteFill>
  );
};

// The background track's volume at a frame: quiet, quieter while the voice speaks, faded in at the start and out at the end.
const BED = 0.2, UNDER = 0.07, RAMP = 9;
const musicVolume = (scenes, fps) => {
  const talk = [];
  let at = 0;
  for (const s of scenes) {
    if (s.talk) talk.push([at, at + s.talk]);
    at += s.frames;
  }
  return (f) => {
    const dip = Math.max(0, ...talk.map(([a, b]) => interpolate(f, [a - RAMP, a, b, b + RAMP], [0, 1, 1, 0], clamp)));
    const edge = interpolate(f, [0, fps, Math.max(fps + 1, at - 2 * fps), Math.max(fps + 2, at)], [0, 1, 1, 0], clamp);
    return (BED - (BED - UNDER) * dip) * edge;
  };
};

export const Video = ({scenes, music, fps = 30}) => {
  let at = 0, left = null;                      // where the pointer was left: the last chained step's target
  return (
    <AbsoluteFill style={{background: C.bg}}>
      {music ? <Audio src={staticFile(`audio/${music}`)} loop volume={musicVolume(scenes, fps)} /> : null}
      {scenes.map((s, i) => {
        const start = at;
        at += s.frames;
        const from = s.chain ? left : null;
        left = targetOf(s) || from;
        const next = scenes[i + 1];
        const hold = Boolean(next?.chain) && near(focusOf(s), focusOf(next), s);
        const look = s.chain && i > 0 && Boolean(scenes[i - 1].before) && near(focusOf(scenes[i - 1]), focusOf(s), s) ? focusOf(scenes[i - 1]) : null;
        return (
          <Sequence key={i} from={start} durationInFrames={s.frames}>
            <Scene s={s} from={from} keep={Boolean(next?.chain)} look={look} hold={hold} />
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};
