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

// A phone's screen (taller than wide) in a landscape video: shown in a phone on the left, with the scene's title and words beside it
// instead of over it. Anything else keeps the full-width layout.
const PHONE = {margin: 34, bezel: 12, gap: 72};
const portraitOf = (s, width, height) => s.kind === 'shot' && Array.isArray(s.size) && s.size[1] > s.size[0] * 1.2 && width > height;

// Where a shot's picture goes: its scale k, its size dw x dh and its top left corner (ox, oy) in the video.
const frameOf = (s, width, height) => {
  const [fw, fh] = s.size;
  if (portraitOf(s, width, height)) {
    const ah = height - 2 * (PHONE.margin + PHONE.bezel);
    const k = Math.min(ah / fh, (width * 0.42) / fw), dw = fw * k, dh = fh * k;
    return {k, dw, dh, ox: Math.round(width * 0.31 - dw / 2), oy: Math.round((height - dh) / 2), phone: true};
  }
  const top = 40 + (s.title ? 52 : 0), aw = width - 128, ah = height - top - 120;
  const k = Math.min(aw / fw, ah / fh), dw = fw * k, dh = fh * k;
  return {k, dw, dh, ox: 64 + (aw - dw) / 2, oy: top + (ah - dh) / 2, phone: false};
};

// The phone around a portrait screen: a dark body with rounded corners, the screen inset by the bezel.
const Phone = ({g}) => (
  <div style={{position: 'absolute', left: g.ox - PHONE.bezel, top: g.oy - PHONE.bezel, width: g.dw + 2 * PHONE.bezel,
               height: g.dh + 2 * PHONE.bezel, borderRadius: 34, background: '#07090b', border: `2px solid ${C.line}`,
               boxShadow: '0 22px 60px rgba(0,0,0,0.55)'}} />
);

// The title and the narration beside a phone.
const Beside = ({s, g}) => {
  const {width, height} = useVideoConfig();
  const r = useRise(4);
  const left = g.ox + g.dw + PHONE.bezel + PHONE.gap;
  return (
    <div style={{position: 'absolute', left, width: width - left - 72, top: 0, height, display: 'flex', flexDirection: 'column',
                 justifyContent: 'center', fontFamily: SANS, opacity: r, transform: `translateX(${(1 - r) * 18}px)`}}>
      {s.title ? <div style={{color: C.text, fontSize: 34, fontWeight: 700, lineHeight: 1.25, marginBottom: 22}}>{s.title}</div> : null}
      {s.title && s.say ? <div style={{width: 56, height: 5, background: C.accent, marginBottom: 22}} /> : null}
      {s.say ? <div style={{color: C.muted, fontSize: 27, lineHeight: 1.45}}>{s.say}</div> : null}
    </div>
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

const arc = Easing.bezier(0.35, 0, 0.15, 1);

// A demo step played out on the real pictures, like a screen recording: the page before it, the pointer gliding in on a slight arc
// (from where the step before left it, when this scene carries on from that one) while the camera pushes in on what the step acts
// on, then a click (a press and a ripple) or a value typed into its field behind a caret, then the frames the run took while the step
// ran (a menu opening, a page loading) in their own time, and the camera easing back out to the whole page.
const Step = ({s, from, keep}) => {
  const f = useCurrentFrame();
  const {width, height, fps} = useVideoConfig();
  const g = frameOf(s, width, height);
  const {k, dw, dh, ox, oy} = g;
  const typing = (s.act === 'fill' || s.act === 'type') && s.box;
  const target = targetOf(s);
  const T = target && [target[0] * k, target[1] * k];
  const F = from && [from[0] * k, from[1] * k];
  const start = T ? (F || [Math.min(dw - 20, T[0] + 160), Math.min(dh - 20, T[1] + 110)]) : F;
  const dist = T ? Math.hypot(T[0] - start[0], T[1] - start[1]) : 0;
  const arrive = T ? 6 + Math.round(Math.max(14, Math.min(26, dist / 25))) : 0;
  const reveal = typing ? Math.max(20, Math.min(75, s.frames - arrive - 34)) : 0;
  const ms = (v) => Math.round((v * fps) / 1000);
  const click = arrive + 3;
  const clip = (s.clip || []).map((c) => ({...c, from: click + ms(c.at)}));
  const done = typing ? arrive + 4 + reveal : s.clip ? click + ms(s.end) : T ? arrive + 2 : 12;

  // The camera: in on the target as the pointer travels, held through the step, back out once its result has been seen.
  const Z = T ? (typing ? 1.6 : 1.5) : 1;
  const out0 = Math.max(arrive + 8, Math.min(done + 18, s.frames - 26));
  const p = T ? ease(f, 2, arrive + 4) * (1 - ease(f, out0, out0 + 22)) : 0;
  const z = T ? 1 + (Z - 1) * p : keep ? 1 : interpolate(f, [0, s.frames], [1, 1.04]);
  const o = T || [dw / 2, dh / 2];
  const pan = (lo, hi, want) => Math.max(lo, Math.min(hi, want));
  const tx = pan(-(z - 1) * (dw - o[0]), (z - 1) * o[0], (dw / 2 - o[0]) * p * 0.6);
  const ty = pan(-(z - 1) * (dh - o[1]), (z - 1) * o[1], (dh / 2 - o[1]) * p * 0.6);

  let pointer = null;
  if (start) {
    const [ex, ey] = T || start;
    const len = Math.hypot(ex - start[0], ey - start[1]) || 1;
    const c = [(start[0] + ex) / 2 + ((ey - start[1]) / len) * len * 0.18, (start[1] + ey) / 2 - ((ex - start[0]) / len) * len * 0.18];
    const m = T ? arc(interpolate(f, [6, arrive], [0, 1], clamp)) : 1;
    const x = (1 - m) ** 2 * start[0] + 2 * (1 - m) * m * c[0] + m ** 2 * ex;
    const y = (1 - m) ** 2 * start[1] + 2 * (1 - m) * m * c[1] + m ** 2 * ey;
    const opacity = F ? 1 : interpolate(f, [4, 10], [0, 1], clamp);
    const press = T ? interpolate(f, [arrive, arrive + 3, arrive + 7], [1, 0.82, 1], clamp) : 1;
    pointer = <Pointer x={x} y={y} opacity={opacity} press={press} />;
  }
  const ring = T ? interpolate(f, [arrive, arrive + 16], [0, 1], clamp) : 1;
  const glow = T ? interpolate(f, [arrive - 8, arrive, arrive + 10, arrive + 18], [0, 0.35, 0.35, 0], clamp) : 0;
  const shown = clip.filter((c) => f >= c.from).pop();
  const after = s.clip ? (f >= done ? 1 : 0) : interpolate(f, [done, done + 6], [0, 1], clamp);
  let typed = null;
  if (typing) {
    const q = interpolate(f, [arrive + 4, arrive + 4 + reveal], [0, 1], clamp);
    const [bx, by, bw, bh] = s.box.map((v) => v * k);
    const [fx, fy, fW, fH] = (s.field || [s.box[0] - 6, s.box[1] - 5, s.box[2] + 12, s.box[3] + 10]).map((v) => v * k);
    const edge = bx + bw * q;
    const focus = interpolate(f, [arrive, arrive + 4, done + 8, done + 16], [0, 1, 1, 0], clamp);
    typed = (
      <>
        <Img src={staticFile(`img/${s.image}`)} style={{position: 'absolute', inset: 0, width: dw, height: dh,
             clipPath: `inset(${by - 2}px ${dw - edge}px ${dh - by - bh - 2}px ${bx - 2}px)`}} />
        <div style={{position: 'absolute', left: fx - 3, top: fy - 3, width: fW + 6, height: fH + 6, borderRadius: 8,
                     border: `2px solid ${C.accent}`, boxShadow: `0 0 0 4px rgba(224,160,48,0.18)`, opacity: focus}} />
        {f >= arrive + 4 && f < done ? (
          <div style={{position: 'absolute', left: edge, top: by + bh * 0.18, width: 2, height: bh * 0.64, background: '#111',
                       opacity: Math.floor(f / 8) % 2 === 0 || q < 1 ? 1 : 0}} />
        ) : null}
      </>
    );
  }
  return (
    <AbsoluteFill style={{fontFamily: SANS}}>
      {s.title && !g.phone ? <div style={{position: 'absolute', left: 64, top: 40, right: 64, color: C.text, fontSize: 30, fontWeight: 700}}>{s.title}</div> : null}
      {g.phone ? <Phone g={g} /> : null}
      <div style={{position: 'absolute', left: ox, top: oy, width: dw, height: dh, overflow: 'hidden', border: g.phone ? 'none' : `1px solid ${C.line}`,
                   borderRadius: g.phone ? 24 : 10, background: C.panel, boxShadow: g.phone ? 'none' : '0 18px 50px rgba(0,0,0,0.45)'}}>
        <div style={{position: 'absolute', inset: 0, transform: `translate(${tx}px, ${ty}px) scale(${z})`, transformOrigin: `${o[0]}px ${o[1]}px`}}>
          <Img src={staticFile(`img/${s.before}`)} style={{position: 'absolute', inset: 0, width: dw, height: dh}} />
          {shown ? <Img src={staticFile(`img/${shown.image}`)} style={{position: 'absolute', inset: 0, width: dw, height: dh}} /> : null}
          {typed}
          <Img src={staticFile(`img/${s.image}`)} style={{position: 'absolute', inset: 0, width: dw, height: dh, opacity: after}} />
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
    </AbsoluteFill>
  );
};

// A still portrait screen: in the phone, slowly pushing in like the landscape picture does.
const PhoneShot = ({s}) => {
  const f = useCurrentFrame();
  const {width, height} = useVideoConfig();
  const g = frameOf(s, width, height);
  const zoom = interpolate(f, [0, s.frames], [1, 1.04]);
  return (
    <AbsoluteFill>
      <Phone g={g} />
      <div style={{position: 'absolute', left: g.ox, top: g.oy, width: g.dw, height: g.dh, overflow: 'hidden', borderRadius: 24, background: C.panel}}>
        <Img src={staticFile(`img/${s.image}`)} style={{width: '100%', height: '100%', objectFit: 'contain', transform: `scale(${zoom})`}} />
      </div>
    </AbsoluteFill>
  );
};

const Shot = ({s, from, keep}) => {
  const {width, height} = useVideoConfig();
  if (s.before) return <Step s={s} from={from} keep={keep} />;
  if (portraitOf(s, width, height)) return <PhoneShot s={s} />;
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
const Scene = ({s, from, keep}) => {
  const Body = KINDS[s.kind];
  const {width, height} = useVideoConfig();
  const phone = portraitOf(s, width, height);
  return (
    <AbsoluteFill style={{background: C.bg, opacity: useFade(s.frames, !s.chain, !keep)}}>
      <Body s={s} from={from} keep={keep} />
      {phone ? <Beside s={s} g={frameOf(s, width, height)} /> : <Subtitle text={s.say} />}
      {s.audio ? <Audio src={staticFile(`audio/${s.audio}`)} /> : null}
    </AbsoluteFill>
  );
};

export const Video = ({scenes}) => {
  let at = 0, left = null;                      // where the pointer was left: the last chained step's target
  return (
    <AbsoluteFill style={{background: C.bg}}>
      {scenes.map((s, i) => {
        const start = at;
        at += s.frames;
        const from = s.chain ? left : null;
        left = targetOf(s) || from;
        return (
          <Sequence key={i} from={start} durationInFrames={s.frames}>
            <Scene s={s} from={from} keep={Boolean(scenes[i + 1]?.chain)} />
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};
