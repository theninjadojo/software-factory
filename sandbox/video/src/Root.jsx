import {Composition} from 'remotion';
import {Video} from './Video.jsx';

// One composition. Its size, frame rate and length come from the props render.mjs passes (the scenes and their frame counts), so
// the bundle built into the image never changes per video.
export const Root = () => (
  <Composition
    id="video"
    component={Video}
    width={1280}
    height={720}
    fps={30}
    durationInFrames={30}
    defaultProps={{scenes: [], width: 1280, height: 720, fps: 30}}
    calculateMetadata={({props}) => ({
      width: props.width,
      height: props.height,
      fps: props.fps,
      durationInFrames: Math.max(1, props.scenes.reduce((n, s) => n + s.frames, 0)),
    })}
  />
);
