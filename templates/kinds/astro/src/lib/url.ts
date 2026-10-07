/**
 * Prefix an internal path with the site's base path, so links keep working when the site is served
 * from a sub-path (a GitHub Pages project page). Always link to internal pages through this.
 */
export function href(path: string, base: string = import.meta.env.BASE_URL): string {
  const prefix = base.endsWith('/') ? base : `${base}/`;
  return prefix + path.replace(/^\/+/, '');
}
