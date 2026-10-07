import type { CollectionEntry } from 'astro:content';

type Post = Pick<CollectionEntry<'blog'>, 'id' | 'data'>;

/** Published posts, newest first. Drafts show in `astro dev` only. */
export function publishedPosts<T extends Post>(posts: T[], includeDrafts = import.meta.env.DEV): T[] {
  return posts
    .filter((post) => includeDrafts || !post.data.draft)
    .sort((a, b) => b.data.pubDate.valueOf() - a.data.pubDate.valueOf());
}

/** Rough reading time in whole minutes (at least 1), at 220 words a minute. */
export function readingMinutes(text: string): number {
  const words = text.trim().split(/\s+/).filter(Boolean).length;
  return Math.max(1, Math.round(words / 220));
}
