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
});
