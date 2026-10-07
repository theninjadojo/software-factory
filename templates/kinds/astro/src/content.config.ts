import { defineCollection } from 'astro:content';
import { glob } from 'astro/loaders';
import { z } from 'astro/zod';

// The schema is checked at build time: a post with missing or wrong front matter fails `astro build`.
const blog = defineCollection({
  loader: glob({ base: './src/content/blog', pattern: '**/*.{md,mdx}' }),
  schema: ({ image }) =>
    z.object({
      title: z.string().min(1).max(90),
      description: z.string().min(1).max(200),
      pubDate: z.coerce.date(),
      updatedDate: z.coerce.date().optional(),
      hero: z.object({ src: image(), alt: z.string().min(1) }).optional(),
      tags: z.array(z.string()).default([]),
      draft: z.boolean().default(false),
    }),
});

export const collections = { blog };
