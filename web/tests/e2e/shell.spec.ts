import { expect, test } from "@playwright/test";

test("loads the local Preact shell", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "控制台骨架" })).toBeVisible();
  await expect(page.getByTestId("status")).toContainText("本地静态资源");
  await expect(page.getByRole("heading", { name: "等待工作区数据" })).toBeVisible();

  const appAsset = await page.request.get("/assets/app.js");
  expect(appAsset.ok()).toBeTruthy();
  expect(await appAsset.text()).toContain("app-shell");

  const licenses = await page.request.get("/THIRD_PARTY_LICENSES.json");
  expect(licenses.ok()).toBeTruthy();
  expect(await licenses.text()).toContain("@xterm/xterm");
});
