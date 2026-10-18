import {AbsoluteFill, Audio, Easing, Img, Sequence, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';

// Fixed templates. Every piece of text arrives as a prop and is rendered as React text (never as HTML), every picture and sound is
// a file render.mjs copied into the bundle's public folder under a name it chose. Nothing here fetches or runs anything else.
const C = {bg: '#12161a', panel: '#1a2026', line: '#2d363d', text: '#e8edf0', muted: '#a9b5bd', add: '#52c7a1', del: '#ff6b5e', hunk: '#8fa8ff', accent: '#e0a030'};
const SANS = "'DejaVu Sans', 'Noto Sans', sans-serif";
const MONO = "'DejaVu Sans Mono', monospace";

const useFade = (frames) => {
  const f = useCurrentFrame();
  return interpolate(f, [0, 10, frames - 10, frames], [0, 1, 1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
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

// A demo step played out on the real pictures: the page before it, the pointer moving to what the step acted on, then a click (a
// ripple, the page after fading in) or a value appearing in its field from left to right behind a caret, as if typed.
const Step = ({s}) => {
  const f = useCurrentFrame();
  const {width, height} = useVideoConfig();
  const [fw, fh] = s.size;
  const top = 40 + (s.title ? 52 : 0), aw = width - 128, ah = height - top - 120;
  const k = Math.min(aw / fw, ah / fh), dw = fw * k, dh = fh * k;
  const ox = 64 + (aw - dw) / 2, oy = top + (ah - dh) / 2;
  const typing = (s.act === 'fill' || s.act === 'type') && s.box;
  const target = typing ? [s.box[0] + Math.min(14, s.box[2] / 4), s.box[1] + s.box[3] / 2] : s.x !== undefined ? [s.x, s.y] : null;
  const arrive = 30;
  const reveal = typing ? Math.max(20, Math.min(75, s.frames - arrive - 34)) : 0;
  const done = typing ? arrive + 4 + reveal : target ? arrive + 2 : 12;
  const after = interpolate(f, [done, done + 10], [0, 1], clamp);
  const zoom = interpolate(f, [0, s.frames], [1, 1.05]);
  const origin = target ? `${target[0] * k}px ${target[1] * k}px` : 'center';
  let pointer = null;
  if (target) {
    const [tx, ty] = [target[0] * k, target[1] * k];
    const [sx, sy] = [Math.min(dw - 20, tx + 160), Math.min(dh - 20, ty + 110)];
    const m = ease(f, 8, arrive);
    const opacity = interpolate(f, [6, 12, done + 8, done + 18], [0, 1, 1, 0], clamp);
    const press = interpolate(f, [arrive, arrive + 3, arrive + 7], [1, 0.82, 1], clamp);
    pointer = <Pointer x={sx + (tx - sx) * m} y={sy + (ty - sy) * m} opacity={opacity} press={press} />;
  }
  const ring = target ? interpolate(f, [arrive, arrive + 16], [0, 1], clamp) : 1;
  let typed = null;
  if (typing) {
    const p = interpolate(f, [arrive + 4, arrive + 4 + reveal], [0, 1], clamp);
    const [bx, by, bw, bh] = s.box.map((v) => v * k);
    const edge = bx + bw * p;
    typed = (
      <>
        <Img src={staticFile(`img/${s.image}`)} style={{position: 'absolute', inset: 0, width: dw, height: dh,
             clipPath: `inset(${by - 2}px ${dw - edge}px ${dh - by - bh - 2}px ${bx - 2}px)`}} />
        {f >= arrive + 4 && f < done ? (
          <div style={{position: 'absolute', left: edge, top: by + bh * 0.18, width: 2, height: bh * 0.64, background: '#111',
                       opacity: Math.floor(f / 8) % 2 === 0 || p < 1 ? 1 : 0}} />
        ) : null}
      </>
    );
  }
  return (
    <AbsoluteFill style={{fontFamily: SANS}}>
      {s.title ? <div style={{position: 'absolute', left: 64, top: 40, right: 64, color: C.text, fontSize: 30, fontWeight: 700}}>{s.title}</div> : null}
      <div style={{position: 'absolute', left: ox, top: oy, width: dw, height: dh, overflow: 'hidden', border: `1px solid ${C.line}`,
                   borderRadius: 10, background: C.panel}}>
        <div style={{position: 'absolute', inset: 0, transform: `scale(${zoom})`, transformOrigin: origin}}>
          <Img src={staticFile(`img/${s.before}`)} style={{position: 'absolute', inset: 0, width: dw, height: dh}} />
          {typed}
          <Img src={staticFile(`img/${s.image}`)} style={{position: 'absolute', inset: 0, width: dw, height: dh, opacity: after}} />
          {target && ring < 1 ? (
            <div style={{position: 'absolute', left: target[0] * k - 26 * ring, top: target[1] * k - 26 * ring, width: 52 * ring,
                         height: 52 * ring, borderRadius: '50%', border: `3px solid ${C.accent}`, opacity: 1 - ring}} />
          ) : null}
          {pointer}
        </div>
      </div>
    </AbsoluteFill>
  );
};

const Shot = ({s}) => {
  if (s.before) return <Step s={s} />;
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

const Scene = ({s}) => {
  const Body = KINDS[s.kind];
  return (
    <AbsoluteFill style={{background: C.bg, opacity: useFade(s.frames)}}>
      <Body s={s} />
      <Subtitle text={s.say} />
      {s.audio ? <Audio src={staticFile(`audio/${s.audio}`)} /> : null}
    </AbsoluteFill>
  );
};

export const Video = ({scenes}) => {
  let at = 0;
  return (
    <AbsoluteFill style={{background: C.bg}}>
      {scenes.map((s, i) => {
        const from = at;
        at += s.frames;
        return (
          <Sequence key={i} from={from} durationInFrames={s.frames}>
            <Scene s={s} />
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};
