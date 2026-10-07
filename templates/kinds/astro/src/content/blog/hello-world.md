---
title: Hello, world
description: The first post on Shikumi App, and how posts are written.
pubDate: 2026-10-01
hero:
  src: ../../assets/first-post.jpg
  alt: An orange and red gradient with soft circles
tags: [news]
---

Posts live in `src/content/blog/` as Markdown or MDX. The front matter is checked against the schema in
`src/content.config.ts`, so a missing title or a malformed date fails the build instead of shipping a broken page.

Images referenced from front matter or imported in MDX go through `astro:assets`: they are resized, converted to
modern formats and given width and height so the page does not shift while it loads.
