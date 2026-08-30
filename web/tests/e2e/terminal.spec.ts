import path from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { bootstrapFixture, taskDetailFixture, vocabularyFixture } from "../fixtures";

const baseline = (name: string) => path.resolve("../tests/baseline", name);

async function stubSnapshots(page: Page) {
  await page.route("**/api/v1/bootstrap", (route) => route.fulfill({ json: bootstrapFixture }));
  await page.route("**/api/v1/vocabulary", (route) => route.fulfill({ json: vocabularyFixture }));
  await page.route("**/api/v1/work/tasks/*", (route) => route.fulfill({ json: taskDetailFixture }));
  await page.route("**/api/v1/timeline?*", (route) => route.fulfill({ json: bootstrapFixture.timeline }));
  await page.route("**/api/v1/members/*/direct", (route) => route.fulfill({
    json: { direct_token: "e2e-direct-ticket", expires_in: 30 },
  }));
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

// BUG(T-025，human 实机复现):镜像连接非拒绝码断线重连后，前端曾经不清
// leaseHeldRef/liveActiveRef——重连是全新服务端连接(has_lease 从 false
// 重新算起)，客户端却还以为自己持有租约，一旦窗口尺寸变化触发 resize
// 上报，新连接会把它当"未持租约写"直接 4401 关闭，界面就地弹出"无法连接
// 成员终端 unauthorized"，跟人一开始有没有点过画面完全无关，看起来像随机
// 触发。这里用假 WS 服务端真实关闭一次连接触发重连，重连成功后立刻改视口
// 尺寸触发 ResizeObserver，断言新连接没有收到任何非白名单写消息。
test("reconnect after a transient drop does not resend a stale resize before re-acquiring the lease", async ({
  page,
}) => {
  const connections: Array<Record<string, unknown>[]> = [];

  await page.routeWebSocket(/\/api\/v1\/terminal\/.+\/mirror$/, (ws) => {
    const received: Record<string, unknown>[] = [];
    connections.push(received);
    const isFirst = connections.length === 1;
    ws.onMessage(async (raw) => {
      const message = JSON.parse(raw as string) as Record<string, unknown>;
      received.push(message);
      if (message.type === "lease" && message.action === "acquire") {
        ws.send(
          JSON.stringify({
            type: "lease_acquired",
            holder: { owner: "web:test:conn-1", host: "test-host", acquired_at: 0 },
          }),
        );
        if (isFirst) {
          // 拿到租约、界面稳定显示"已持有"之后再来一次普通断线(非 4000+
          // 拒绝码)，模拟网络抖动/服务重启，触发客户端自动重连。
          await new Promise((resolve) => setTimeout(resolve, 300));
          ws.close();
        }
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
  await page.locator(".terminal-surface").click();
  await expect(page.getByText("已持有交互租约")).toBeVisible();

  await expect.poll(() => connections.length).toBe(2);
  // 重连成功后改视口尺寸触发 ResizeObserver;有 bug 时会把 resize 发给
  // 这条从没 acquire 过租约的新连接。
  await page.setViewportSize({ width: 1200, height: 900 });
  await page.waitForTimeout(300);

  const secondConnection = connections[1];
  expect(secondConnection).toEqual([]);
});

test("member card in the sidebar navigates to that member's terminal", async ({ page }) => {
  await page.routeWebSocket(/\/api\/v1\/terminal\/.+\/mirror$/, () => undefined);
  await page.goto("/");
  await page.locator(".member-card", { hasText: "sol" }).click();
  await expect(page).toHaveURL(/\/member\/sol\/terminal$/);
  await expect(page.getByRole("heading", { name: "终端镜像" })).toBeVisible();
});

test("member controls use double-submit HTTP and full attach sends its ticket first", async ({ page }) => {
  const controlRequests: Array<{ path: string; token: string | null }> = [];
  const attachFrames: Array<Record<string, unknown>> = [];

  await page.routeWebSocket(/\/api\/v1\/terminal\/.+\/mirror$/, (ws) => {
    ws.send(JSON.stringify({
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
      data: "$ sol ready\r\n",
    }));
  });
  await page.route(/\/api\/v1\/members\/sol\/(interrupt|restart(?:\/confirm)?|attach)$/, async (route) => {
    const url = new URL(route.request().url());
    controlRequests.push({
      path: url.pathname,
      token: await route.request().headerValue("x-amux-session"),
    });
    if (url.pathname.endsWith("/restart/confirm")) {
      await route.fulfill({ json: { confirm_token: "restart-confirm", expires_in: 30 } });
    } else if (url.pathname.endsWith("/attach")) {
      await route.fulfill({ json: { attach_token: "attach-ticket", expires_in: 30 } });
    } else {
      await route.fulfill({ json: { action: "interrupt", changed: true, detail: "已执行" } });
    }
  });
  await page.routeWebSocket(/\/api\/v1\/terminal\/.+\/attach$/, (ws) => {
    ws.onMessage((raw) => {
      if (typeof raw !== "string") return;
      const message = JSON.parse(raw) as Record<string, unknown>;
      attachFrames.push(message);
      if (message.type === "attach") {
        ws.send(JSON.stringify({
          type: "attached",
          holder: { owner: "web-attach:e2e", host: "test", acquired_at: 0 },
        }));
        ws.send(Buffer.from("\u001b[32mPTY attached to sol\u001b[0m\r\n"));
      } else if (message.type === "exit") {
        ws.close();
      }
    });
  });

  page.on("dialog", (dialog) => dialog.accept());
  await page.goto("/member/sol/terminal");
  await page.getByRole("button", { name: "打断" }).click();
  await expect(page.getByText("已执行")).toBeVisible();
  await page.getByRole("button", { name: "/restart" }).click();
  await expect.poll(() => controlRequests.filter((item) => item.path.includes("restart")).length).toBe(2);
  expect(controlRequests.every((item) => item.token === "fixture-write-token")).toBe(true);

  await page.getByRole("button", { name: "完整接管" }).click();
  await expect(page.getByRole("dialog", { name: "sol 完整接管" })).toBeVisible();
  await expect(page.locator(".attach-surface")).toContainText("PTY attached to sol");
  await expect.poll(() => attachFrames.some((item) => item.type === "attach")).toBe(true);
  const first = attachFrames[0];
  expect(first.attach_token).toBe("attach-ticket");
  expect(first).not.toHaveProperty("actor");

  await page.screenshot({ path: baseline("web-008-attach-1440x1000.png"), fullPage: true });
});

test("workspace member management shows persistent and process-local members", async ({ page }) => {
  let addedWithToken: string | null = null;
  await page.route("**/api/v1/member-management", (route) => route.fulfill({
    json: {
      members: [
        { name: "sol", source: "roster", temporary: false, muted: false, running: true },
        { name: "helper", source: "adopted", temporary: true, muted: true, running: true },
      ],
      adoptable: [{ name: "reviewer", commands: ["codex"] }],
      presets: ["claude", "codex", "gemini", "sol"],
    },
  }));
  await page.route("**/api/v1/members", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    addedWithToken = await route.request().headerValue("x-amux-session");
    await route.fulfill({ json: { name: "claude", created: true } });
  });

  await page.goto("/workspace");
  await expect(page.getByRole("heading", { name: "成员管理" })).toBeVisible();
  await expect(page.getByText("临时收编 · 仅本进程有效")).toBeVisible();
  await expect(page.getByText("重启即失效")).toBeVisible();
  await page.getByRole("button", { name: "加入成员" }).click();
  await expect.poll(() => addedWithToken).toBe("fixture-write-token");
  await page.screenshot({ path: baseline("web-008-member-management-1440x1000.png"), fullPage: true });
});
