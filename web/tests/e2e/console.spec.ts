import path from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { bootstrapFixture, taskDetailFixture, vocabularyFixture } from "../fixtures";

const baseline = (name: string) => path.resolve("../tests/baseline", name);

async function stubSnapshots(page: Page) {
  await page.route("**/api/v1/bootstrap", (route) => route.fulfill({ json: bootstrapFixture }));
  await page.route("**/api/v1/vocabulary", (route) => route.fulfill({ json: vocabularyFixture }));
  await page.route("**/api/v1/work/tasks/*", (route) => route.fulfill({ json: taskDetailFixture }));
  await page.route("**/api/v1/timeline?*", (route) => route.fulfill({ json: bootstrapFixture.timeline }));
  await page.route("**/api/v1/attachments", (route) => route.fulfill({
    json: {
      attachment: {
        id: "0123456789abcdef",
        name: "clipboard-0123456789abcdef.png",
        media_type: "image/png",
        width: 32,
        height: 20,
        size: 128,
        download_url: "/api/v1/attachments/0123456789abcdef",
      },
    },
  }));
  await page.route("**/api/v1/messages", (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    const kind = String(body.kind ?? "message");
    return route.fulfill({
      json: {
        ok: true,
        message: {
          id: "sent-message-id",
          to: kind === "reply" ? "fable" : String(body.to ?? "fable"),
          kind,
          reply_to: body.reply_to ?? null,
          task_id: body.task_id ?? null,
          attachment_ids: body.attachment_ids ?? [],
        },
      },
    });
  });
}

test.beforeEach(async ({ page }) => {
  await stubSnapshots(page);
  await page.addInitScript(() => {
    localStorage.clear();
    localStorage.setItem("amux.web.last-seen", JSON.stringify({ epoch: "4f4b5d2a88d31001", seq: 38 }));
    localStorage.setItem("amux.web.theme", "dark");
  });
});

test("task board composes task-linked text, images and ask messages", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "WEB-006 消息、ask/reply 与浏览器附件" })).toBeVisible();
  await expect(page.getByText("不可覆盖事件流")).toBeVisible();
  await expect(page.getByText("关联沟通")).toBeVisible();
  await expect(page.getByText("运行状态降级")).toBeVisible();
  await expect(page.getByLabel("3 条未读")).toBeVisible();
  await expect(page.getByLabel("工作对话输入")).toBeVisible();

  await page.locator('input[type="file"]').setInputFiles({
    name: "evidence.png",
    mimeType: "image/png",
    buffer: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=", "base64"),
  });
  await expect(page.getByText("0123456789abcdef")).toBeVisible();
  await page.getByRole("textbox").fill("已补齐浏览器消息与附件闭环");
  await page.screenshot({ path: baseline("web-006-compose-1440x1000.png") });

  const messageRequest = page.waitForRequest("**/api/v1/messages");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  const messageBody = (await messageRequest).postDataJSON() as Record<string, unknown>;
  expect(messageBody).toMatchObject({
    kind: "message",
    to: "fable",
    task_id: "T-016",
    attachment_ids: ["0123456789abcdef"],
  });
  expect(messageBody).not.toHaveProperty("actor");
  expect(messageBody).not.toHaveProperty("path");
  await expect(page.getByText("消息已发送给 fable")).toBeVisible();

  await page.getByRole("button", { name: "Ask", exact: true }).click();
  await page.getByRole("textbox").fill("@so");
  await expect(page.getByRole("option", { name: "@sol" })).toBeVisible();
  await page.getByRole("textbox").press("Tab");
  await page.getByRole("textbox").fill("可以独立复核吗？");
  const askRequest = page.waitForRequest("**/api/v1/messages");
  await page.getByRole("button", { name: "发送 Ask" }).click();
  const askBody = (await askRequest).postDataJSON() as Record<string, unknown>;
  expect(askBody).toMatchObject({ kind: "ask", to: "sol" });
  expect(askBody).not.toHaveProperty("task_id");
});

test("timeline reply, filters, workspace, help and safe exit form a navigation loop", async ({ page }) => {
  await page.goto("/timeline");
  await expect(page.getByRole("heading", { name: "工作对话时间线" })).toBeVisible();
  await page.getByRole("button", { name: "回复 ask" }).click();
  await expect(page.getByText("回复 ask · msg-37")).toBeVisible();
  expect(await page.evaluate(() => window.scrollY)).toBe(0);
  await expect(page.locator(".app-header .brand")).toBeVisible();
  await page.screenshot({ path: baseline("web-006-reply-1440x1000.png") });
  await page.getByRole("textbox").fill("可以，按现有控制面继续。");
  const replyRequest = page.waitForRequest("**/api/v1/messages");
  await page.getByRole("button", { name: "回复", exact: true }).click();
  const replyBody = (await replyRequest).postDataJSON() as Record<string, unknown>;
  expect(replyBody).toMatchObject({ kind: "reply", reply_to: "msg-37" });
  expect(replyBody).not.toHaveProperty("to");
  expect(replyBody).not.toHaveProperty("task_id");

  await page.getByRole("button", { name: /控制事件/ }).click();
  await expect(page.getByText("tmux session missing")).toBeVisible();

  await page.getByRole("button", { name: "工作区", exact: true }).click();
  await expect(page.getByRole("heading", { name: "proj-multi-agent" })).toBeVisible();
  await page.keyboard.press("?");
  await expect(page.getByRole("heading", { name: "快捷导航" })).toBeVisible();
  await page.keyboard.press("t");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.keyboard.press("F3");
  await expect(page).toHaveURL(/\/task\/T-016$/);

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
