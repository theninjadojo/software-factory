import { expect, test, type Page } from "@playwright/test";

// The built app calls the API at VITE_API_URL (http://localhost:8000 unless set at build time).
// Every request to /items, on any host, is answered here by a small in-memory fake.
async function fakeApi(page: Page, items: { id: number; name: string; created_at: string; processed_at: null }[] = []) {
  await page.route(/\/items$/, async (route) => {
    const request = route.request();
    if (request.method() === "POST") {
      const { name } = request.postDataJSON() as { name: string };
      const item = { id: items.length + 1, name, created_at: new Date().toISOString(), processed_at: null };
      items.unshift(item);
      return route.fulfill({ status: 201, json: item });
    }
    return route.fulfill({ json: items });
  });
}

test("lists items and adds one", async ({ page }) => {
  await fakeApi(page, [{ id: 1, name: "Existing item", created_at: "2026-01-02T10:00:00Z", processed_at: null }]);
  await page.goto("/");

  const list = page.getByRole("list", { name: "Items" });
  await expect(list.getByText("Existing item")).toBeVisible();

  await page.getByLabel("Name").fill("Added in the browser");
  await page.getByRole("button", { name: "Add item" }).click();

  await expect(list.getByText("Added in the browser")).toBeVisible();
  await expect(page.getByLabel("Name")).toHaveValue("");
});

test("says so when the API is down", async ({ page }) => {
  await page.route(/\/items$/, (route) => route.fulfill({ status: 503, json: { detail: "database unavailable" } }));
  await page.goto("/");
  await expect(page.getByRole("alert")).toContainText("database unavailable");
});

test("unknown paths show the not-found page", async ({ page }) => {
  await page.goto("/no-such-page");
  await expect(page.getByRole("heading", { name: "Page not found" })).toBeVisible();
});
