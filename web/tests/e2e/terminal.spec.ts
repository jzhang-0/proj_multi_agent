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
    localStorage.setItem("amux.web.last-seen", JSON.stringify({ epoch: "4f4b5d2a88d31001", seq: 41 }));
    localStorage.setItem("amux.web.theme", "dark");
  });
});

// WEB-007:真实鉴权与真实 WS 后端不在 `npm run serve` 这套 Playwright harness
// 覆盖范围内(serve.mjs 是无鉴权的纯静态服务器，见 docs 调研)；镜像通道的
// 抢占/回滚/断线释放/并发观看四个场景已在 tests/test_web_terminal.py 用真实
// tmux + 真实 WS 覆盖。这里只做前端本身该负责的事:渲染 ANSI 帧、发送协议
// 消息、走完点击直连的一次往返，并留一张视觉自验证截图。
test("member terminal renders a mirrored frame and completes click-to-connect", async ({ page }) => {
  const received: Array<Record<string, unknown>> = [];

  await page.routeWebSocket(/\/api\/v1\/terminal\/.+\/mirror$/, (ws) => {
    ws.onMessage((raw) => {
      const message = JSON.parse(raw as string) as Record<string, unknown>;
      received.push(message);
      if (message.type === "lease" && message.action === "acquire") {
        ws.send(
          JSON.stringify({
            type: "lease_acquired",
            holder: { owner: "web:test:conn-1", host: "test-host", acquired_at: 0 },
          }),
        );
      } else if (message.type === "focus_input") {
        ws.send(JSON.stringify({ type: "live", active: true }));
      }
    });
    ws.send(
      JSON.stringify({
        type: "frame",
        member: "sol",
        frame_seq: 1,
        cols: 80,
        rows: 24,
        history_offset: 0,
        captured_at: 0,
        cursor_y: 2,
        input_rows: [2],
        live_allowed: true,
        encoding: "ansi",
        data: "$ echo hello-from-sol\r\nhello-from-sol\r\n",
      }),
    );
  });

  await page.goto("/member/sol/terminal");
  await expect(page.getByRole("heading", { name: "终端镜像" })).toBeVisible();
  await expect(page.getByText("只读镜像")).toBeVisible();
  await expect(page.locator(".terminal-surface")).toContainText("hello-from-sol");

  await page.locator(".terminal-surface").click();
  await expect(page.getByText("已持有交互租约")).toBeVisible();
  await expect(page.getByText("直连输入中")).toBeVisible();

  await page.screenshot({ path: baseline("web-007-terminal-1440x1000.png"), fullPage: true });

  await expect
    .poll(() => received.some((message) => message.type === "lease" && message.action === "acquire"))
    .toBe(true);
  await expect
    .poll(() => received.some((message) => message.type === "focus_input"))
    .toBe(true);
});

test("member card in the sidebar navigates to that member's terminal", async ({ page }) => {
  await page.routeWebSocket(/\/api\/v1\/terminal\/.+\/mirror$/, () => undefined);
  await page.goto("/");
  await page.locator(".member-card", { hasText: "sol" }).click();
  await expect(page).toHaveURL(/\/member\/sol\/terminal$/);
  await expect(page.getByRole("heading", { name: "终端镜像" })).toBeVisible();
});
