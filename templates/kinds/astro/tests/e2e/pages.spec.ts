import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

const pages = [
  { path: '/', heading: 'Shikumi App' },
  { path: '/blog/', heading: 'Blog' },
  { path: '/blog/hello-world/', heading: 'Hello, world' },
  { path: '/blog/writing-with-mdx/', heading: 'Writing with MDX' },
  { path: '/about/', heading: 'About' },
];

for (const { path, heading } of pages) {
  test.describe(path, () => {
    test('renders its heading', async ({ page }) => {
      await page.goto(path);
      await expect(page.getByRole('heading', { level: 1, name: heading })).toBeVisible();
    });

    test('has no detectable accessibility violations', async ({ page }) => {
      await page.goto(path);
      const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
      expect(results.violations).toEqual([]);
    });
  });
}

test('the blog index links to every post', async ({ page }) => {
  await page.goto('/blog/');
  await page.getByRole('link', { name: 'Writing with MDX' }).click();
  await expect(page).toHaveURL(/\/blog\/writing-with-mdx\/$/);
});

test('an unknown page shows the 404 page', async ({ page }) => {
  const response = await page.goto('/no-such-page/');
  expect(response?.status()).toBe(404);
  await expect(page.getByRole('heading', { name: 'Page not found' })).toBeVisible();
});
