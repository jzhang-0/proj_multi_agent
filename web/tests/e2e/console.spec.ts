import path from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { bootstrapFixture, taskDetailFixture, vocabularyFixture } from "../fixtures";

const baseline = (name: string) => path.resolve("../tests/baseline", name);

async function stubSnapshots(page: Page) {
  await page.route("**/api/v1/bootstrap", (route) => route.fulfill({ json: bootstrapFixture }));
  await page.route("**/api/v1/vocabulary", (route) => route.fulfill({ json: vocabularyFixture }));
  await page.route("**/api/v1/work/tasks/*", (route) => route.fulfill({ json: taskDetailFixture }));
  await page.route("**/api/v1/timeline?*", (route) => route.fulfill({ json: bootstrapFixture.timeline }));
}

test.beforeEach(async ({ page }) => {
  await stubSnapshots(page);
  await page.addInitScript(() => {
    localStorage.clear();
    localStorage.setItem("amux.web.last-seen", JSON.stringify({ epoch: "4f4b5d2a88d31001", seq: 38 }));
    localStorage.setItem("amux.web.theme", "dark");
  });
});

test("task board exposes the complete read-only work surface", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "WEB-005 桌面 SPA 只读与导航闭环" })).toBeVisible();
  await expect(page.getByText("不可覆盖事件流")).toBeVisible();
  await expect(page.getByText("关联沟通")).toBeVisible();
  await expect(page.getByText("运行状态降级")).toBeVisible();
  await expect(page.getByLabel("3 条未读")).toBeVisible();
  await page.screenshot({ path: baseline("web-005-task-board-1440x1000.png"), fullPage: true });
});

test("timeline filters, workspace, help and safe exit form a navigation loop", async ({ page }) => {
  await page.goto("/timeline");
  await expect(page.getByRole("heading", { name: "工作对话时间线" })).toBeVisible();
  await page.screenshot({ path: baseline("web-005-timeline-1440x1000.png"), fullPage: true });
  await page.getByRole("button", { name: /控制事件/ }).click();
  await expect(page.getByText("tmux session missing")).toBeVisible();

  await page.getByRole("button", { name: "工作区", exact: true }).click();
  await expect(page.getByRole("heading", { name: "proj-multi-agent" })).toBeVisible();
  await page.keyboard.press("?");
  await expect(page.getByRole("heading", { name: "快捷导航" })).toBeVisible();
  await page.keyboard.press("t");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.keyboard.press("F3");
  await expect(page).toHaveURL(/\/task\/T-014$/);

  await page.getByRole("button", { name: "退出", exact: true }).click();
  await expect(page.getByRole("heading", { name: "观察会话已退出" })).toBeVisible();
  await expect(page.getByText("成员与任务继续运行")).toBeVisible();
});

test("built output keeps package assets self contained", async ({ page }) => {
  await page.goto("/");
  const appAsset = await page.request.get("/assets/app.js");
  expect(appAsset.ok()).toBeTruthy();
  expect(await appAsset.text()).toContain("amux.web.last-seen");

  const licenses = await page.request.get("/THIRD_PARTY_LICENSES.json");
  expect(licenses.ok()).toBeTruthy();
  expect(await licenses.text()).toContain("@xterm/xterm");
});
