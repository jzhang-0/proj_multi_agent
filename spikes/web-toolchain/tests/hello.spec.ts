import { expect, test } from "@playwright/test";

test("bundled Preact page and xterm mount locally", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Web toolchain spike" })).toBeVisible();
  await expect(page.getByTestId("status")).toContainText("bundled locally");
  await expect(page.locator("#terminal .xterm-screen")).toBeVisible();
  const appAsset = await page.request.get("/assets/app.js");
  expect(appAsset.ok()).toBeTruthy();
  expect(await appAsset.text()).toContain("xterm");
  await expect(page.locator("#terminal .xterm")).toHaveClass(/xterm/);
});
