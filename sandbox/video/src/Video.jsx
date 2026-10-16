import {AbsoluteFill, Audio, Img, Sequence, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';

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

const Shot = ({s}) => {
  const f = useCurrentFrame();
  const zoom = interpolate(f, [0, s.frames], [1, 1.06]);
  return (
    <AbsoluteFill style={{padding: '40px 64px 120px', fontFamily: SANS}}>
      {s.title ? <div style={{color: C.text, fontSize: 30, fontWeight: 700, marginBottom: 16}}>{s.title}</div> : null}
      <div style={{flex: 1, overflow: 'hidden', border: `1px solid ${C.line}`, borderRadius: 10, background: C.panel}}>
        <Img src={staticFile(`img/${s.image}`)} style={{width: '100%', height: '100%', objectFit: 'contain', transform: `scale(${zoom})`}} />
      </div>
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
