// Checks every internal link, image, asset and #fragment of the built site in dist/. Run after `npm run build`.
// It works offline: links to other sites are skipped (the canonical URL and the sitemap point at SITE_URL).
import { LinkChecker } from 'linkinator';

let origin; // the local server linkinator starts for dist/: the first URL it asks about
const result = await new LinkChecker().check({
  path: 'dist',
  recurse: true,
  checkFragments: true,
  linksToSkip: async (url) => {
    origin ??= new URL(url).origin;
    return new URL(url).origin !== origin;
  },
});

const checked = result.links.filter((link) => link.state !== 'SKIPPED');
const broken = result.links.filter((link) => link.state === 'BROKEN');
console.log(`Checked ${checked.length} links, ${broken.length} broken.`);
for (const link of broken) console.error(`  ${link.status} ${link.url} (on ${link.parent})`);
process.exit(result.passed ? 0 : 1);
