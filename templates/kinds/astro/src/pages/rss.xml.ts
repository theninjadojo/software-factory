import rss from '@astrojs/rss';
import type { APIContext } from 'astro';
import { getCollection } from 'astro:content';
import { publishedPosts } from '../lib/posts';
import { SITE } from '../lib/site';
import { href } from '../lib/url';

export async function GET(context: APIContext) {
  const posts = publishedPosts(await getCollection('blog'));
  return rss({
    title: SITE.title,
    description: SITE.description,
    site: context.site ?? 'http://localhost:4321',
    items: posts.map((post) => ({
      title: post.data.title,
      description: post.data.description,
      pubDate: post.data.pubDate,
      link: href(`/blog/${post.id}/`),
    })),
  });
}
