// Run once at image build: bundles the composition in src/ into /app/bundle, which render.mjs copies and serves (from localhost) at
// render time. Nothing is bundled at render time.
import {bundle} from '@remotion/bundler';

const out = await bundle({entryPoint: new URL('./src/index.jsx', import.meta.url).pathname, outDir: '/app/bundle',
                          publicDir: new URL('./public', import.meta.url).pathname});
console.log(`bundled to ${out}`);
