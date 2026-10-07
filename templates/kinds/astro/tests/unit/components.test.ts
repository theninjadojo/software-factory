import { experimental_AstroContainer as AstroContainer } from 'astro/container';
import { describe, expect, it } from 'vitest';
import FormattedDate from '../../src/components/FormattedDate.astro';
import PostCard from '../../src/components/PostCard.astro';

describe('components', () => {
  it('FormattedDate renders a machine-readable date', async () => {
    const container = await AstroContainer.create();
    const html = await container.renderToString(FormattedDate, { props: { date: new Date('2026-10-01') } });
    expect(html).toContain('datetime="2026-10-01T00:00:00.000Z"');
    expect(html).toContain('1 October 2026');
  });

  it('PostCard links to the post', async () => {
    const container = await AstroContainer.create();
    const html = await container.renderToString(PostCard, {
      props: { id: 'hello-world', title: 'Hello', description: 'A post', pubDate: new Date('2026-10-01') },
    });
    expect(html).toContain('href="/blog/hello-world/"');
    expect(html).toContain('Hello');
  });
});
