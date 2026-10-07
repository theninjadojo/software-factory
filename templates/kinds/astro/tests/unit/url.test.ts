import { describe, expect, it } from 'vitest';
import { href } from '../../src/lib/url';

describe('href', () => {
  it('keeps root paths at the site root', () => {
    expect(href('/blog/', '/')).toBe('/blog/');
  });

  it('prefixes the base path of a project page', () => {
    expect(href('/blog/', '/shikumi-app')).toBe('/shikumi-app/blog/');
    expect(href('about/', '/shikumi-app/')).toBe('/shikumi-app/about/');
  });
});
