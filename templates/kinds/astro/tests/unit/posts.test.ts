import { describe, expect, it } from 'vitest';
import { publishedPosts, readingMinutes } from '../../src/lib/posts';

const post = (id: string, pubDate: string, draft = false) => ({
  id,
  data: { title: id, description: id, pubDate: new Date(pubDate), tags: [], draft },
});

describe('publishedPosts', () => {
  it('sorts newest first and leaves out drafts', () => {
    const posts = [post('old', '2026-01-01'), post('draft', '2026-03-01', true), post('new', '2026-02-01')];
    expect(publishedPosts(posts, false).map((p) => p.id)).toEqual(['new', 'old']);
  });

  it('keeps drafts when asked', () => {
    expect(publishedPosts([post('draft', '2026-03-01', true)], true)).toHaveLength(1);
  });
});

describe('readingMinutes', () => {
  it('is at least one minute', () => {
    expect(readingMinutes('')).toBe(1);
  });

  it('counts about 220 words a minute', () => {
    expect(readingMinutes('word '.repeat(660))).toBe(3);
  });
});
