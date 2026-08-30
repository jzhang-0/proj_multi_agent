import path from "node:path";
import { execFileSync } from "node:child_process";
import { expect, test } from "@playwright/test";

const baseURL = process.env.AMUX_REAL_WEB_BASE_URL;
const token = process.env.AMUX_REAL_WEB_TOKEN;
const workspaceSlug = process.env.AMUX_REAL_WORKSPACE_SLUG ?? "proj_multi_agent";
const members = (process.env.AMUX_REAL_MEMBERS ?? "")
  .split(",")
  .map((member) => member.trim())
  .filter(Boolean);
const screenshotLabel = process.env.AMUX_REAL_SCREENSHOT_LABEL ?? "after";
const baseline = (member: string) => path.resolve(
  "../tests/baseline",
  `web-007-real-${screenshotLabel}-${member}-1440x1000.png`,
);

test.describe("real tmux terminal mirror", () => {
  test.skip(
    !baseURL || !token || members.length === 0,
    "set AMUX_REAL_WEB_BASE_URL, AMUX_REAL_WEB_TOKEN and AMUX_REAL_MEMBERS",
  );

  test("renders live Claude/Codex member sessions", async ({ page }) => {
    await page.goto(`/?token=${encodeURIComponent(token ?? "")}`);
    await expect(page.getByRole("navigation", { name: "主导航" })).toBeVisible();

    for (const member of members) {
      const capture = execFileSync(
        "tmux",
        ["capture-pane", "-p", "-t", `${member}@${workspaceSlug}`],
        { encoding: "utf8" },
      );
      expect(capture.trim(), `${member} must be a real, non-empty tmux member`).not.toBe("");

      await page.goto(`/member/${encodeURIComponent(member)}/terminal`);
      await expect(page.getByRole("heading", { name: "终端镜像" })).toBeVisible();
      await expect(page.locator(".terminal-surface .xterm-rows > div").first()).toBeVisible();
      await expect.poll(
        async () => (await page.locator(".terminal-surface .xterm-rows").innerText()).trim(),
        { message: `${member} mirror should render the live tmux frame` },
      ).not.toBe("");
      await page.screenshot({ path: baseline(member), fullPage: true });
    }
  });

  // BUG(T-025，human 实机复现，opus 复审要求真实环境取证):合成 WS 服务端能
  // 证明前端状态机改对了，证不了真实 cookie/`/api/v1/stream`/租约链路已经
  // 通。这里对一个真实运行成员走完整链路:连上看到真实画面 → 点击画面拿到
  // 真实交互租约(确认进直连态,缺陷前提——没有"曾经持有过租约"就没有陈旧
  // 状态可清)→ 制造一次非拒绝码断线 → 等浏览器自动重连 → 改视口尺寸触发
  // `ResizeObserver` → 断言画面老实退回只读镜像、没有"无法连接成员终端"
  // 横幅。
  //
  // 断线手段的取舍:先试过 `context.setOffline()` 切 6s 再切回，localhost
  // 连接期间没有任何一侧尝试收发数据，浏览器并不会因此判定已有 WS 连接
  // 失效(离线开关只挡新连接，不会主动掐断已建立的 socket)，实测确实没
  // 触发过 onclose。改成直接对页面里那个真实 WebSocket 对象调用一次
  // `close()`(默认码 1000，正常关闭，同样 < 4000 的"非拒绝码"分支)——这
  // 是同一个真实成员会话上真实浏览器创建的真实 socket，触发的是与网络抖动
  // 完全相同的 `connectTerminalMirror.onclose` 重连路径，只是断开的触发方式
  // 更确定，不依赖 localhost 网络仿真的时序不确定性。
  test("recovers from a real network drop without leaking stale lease state", async ({
    page,
  }) => {
    const member = members[0];
    await page.addInitScript(() => {
      const NativeWebSocket = window.WebSocket;
      (window as unknown as { __testSockets: WebSocket[] }).__testSockets = [];
      window.WebSocket = new Proxy(NativeWebSocket, {
        construct(target, args) {
          const instance = new (target as unknown as new (...a: unknown[]) => WebSocket)(...args);
          (window as unknown as { __testSockets: WebSocket[] }).__testSockets.push(instance);
          return instance;
        },
      }) as unknown as typeof WebSocket;
    });
    await page.goto(`/?token=${encodeURIComponent(token ?? "")}`);
    await page.goto(`/member/${encodeURIComponent(member)}/terminal`);
    await expect(page.getByRole("heading", { name: "终端镜像" })).toBeVisible();
    await expect.poll(
      async () => (await page.locator(".terminal-surface .xterm-rows").innerText()).trim(),
      { message: `${member} mirror should render the live tmux frame before the drop` },
    ).not.toBe("");

    // 真实点击拿真实租约(短暂持有，不发任何按键，不影响成员会话内容)。
    await page.locator(".terminal-surface").click();
    await expect(page.getByText("已持有交互租约")).toBeVisible({ timeout: 10_000 });
    await page.screenshot({
      path: baseline(`${member}-reconnect-1-lease-held`),
      fullPage: true,
    });

    await page.evaluate(() => {
      const sockets = (window as unknown as { __testSockets: WebSocket[] }).__testSockets;
      const mirrorSocket = sockets.find((socket) => socket.url.includes("/mirror"));
      if (!mirrorSocket) throw new Error("mirror WebSocket not found on window.__testSockets");
      mirrorSocket.close(); // 默认码 1000，< 4000 的"非拒绝码"分支。
    });

    // 重连后核心断言:回到只读镜像(不是继续显示直连态)、没有 unauthorized
    // 横幅——这正是本次修法要保证的"老实退回只读，等用户重新点击拿租约"。
    await expect(page.getByText("无法连接成员终端")).toHaveCount(0, { timeout: 15_000 });
    await expect(page.getByText("只读镜像(点击画面获取控制权)")).toBeVisible({
      timeout: 15_000,
    });

    await page.setViewportSize({ width: 1200, height: 900 });
    await page.waitForTimeout(500);
    await expect(page.getByText("无法连接成员终端")).toHaveCount(0);
    await expect.poll(
      async () => (await page.locator(".terminal-surface .xterm-rows").innerText()).trim(),
      { message: `${member} mirror should still render after reconnect + resize` },
    ).not.toBe("");
    await page.screenshot({
      path: baseline(`${member}-reconnect-2-after-drop-resize`),
      fullPage: true,
    });
  });
});
