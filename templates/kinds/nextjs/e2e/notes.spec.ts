import { expect, test } from "@playwright/test";

test("health reports the database is reachable", async ({ request }) => {
  const response = await request.get("/api/health");
  expect(response.ok()).toBeTruthy();
  expect(await response.json()).toEqual({ status: "ok" });
});

test("a new note appears in the list", async ({ page }) => {
  const title = `Note ${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
  await page.goto("/");

  await page.getByLabel("Title").fill(title);
  await page.getByLabel("Note", { exact: true }).fill("Written by the end-to-end test");
  await page.getByRole("button", { name: "Add note" }).click();

  const notes = page.getByRole("list", { name: "Notes" });
  await expect(notes.getByText(title)).toBeVisible();
  await page.reload();
  await expect(notes.getByText(title)).toBeVisible();
});

test("a note needs a title", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Add note" }).click();
  await expect(page.getByText("Give the note a title")).toBeVisible();
});

test("the account page sends signed-out visitors home", async ({ page }) => {
  await page.goto("/account");
  await expect(page).toHaveURL("/");
});
