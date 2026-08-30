import { execFileSync, spawnSync } from "node:child_process";
import { afterEach, describe, expect, it } from "vitest";
import { MIRROR_TERMINAL_OPTIONS } from "../../src/terminal-render";

const HAS_TMUX = spawnSync("tmux", ["-V"], { encoding: "utf8" }).status === 0;
const socketName = `web007-render-${process.pid}`;
const sessionName = "wide-frame";

function tmux(...args: string[]): string {
  return execFileSync("tmux", ["-L", socketName, ...args], { encoding: "utf8" });
}

function shellQuote(value: string): string {
  return `'${value.replaceAll("'", `'\\''`)}'`;
}

async function captureWhenReady(marker: string): Promise<string> {
  for (let attempt = 0; attempt < 20; attempt += 1) {
    const capture = tmux("capture-pane", "-p", "-t", sessionName);
    if (capture.includes(marker)) return capture.endsWith("\n") ? capture.slice(0, -1) : capture;
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
  throw new Error(`tmux frame never contained ${marker}`);
}

afterEach(() => {
  if (HAS_TMUX) spawnSync("tmux", ["-L", socketName, "kill-server"]);
});

describe.skipIf(!HAS_TMUX)("terminal mirror rendering", () => {
  it("keeps every real tmux capture row at column zero with CJK, emoji and wrapping", async () => {
    // xterm initializes its color parser through a 2D canvas even when the
    // terminal is not opened. jsdom intentionally omits canvas rendering; this
    // minimal style round-trip is all that parser needs for a buffer-only test.
    Object.defineProperty(HTMLCanvasElement.prototype, "getContext", {
      configurable: true,
      value: () => ({ fillStyle: "#000000" }),
    });
    const { Terminal } = await import("@xterm/xterm");
    const cols = 48;
    const rows = 12;
    const fixtureRows = [
      "ASCII starts at the left edge",
      "╭─ Claude 成员终端 ─────────────╮",
      "│ 中文宽字符：你好，世界 🙂      │",
      "│ emoji: 🚀 ✅  box: ├──┤        │",
      `超宽行:${"0123456789".repeat(8)}`,
      "TAIL-LEFT",
    ];
    const command = `printf '%s\\n' ${fixtureRows.map(shellQuote).join(" ")}; exec sleep 30`;
    tmux(
      "new-session", "-d", "-x", String(cols), "-y", String(rows),
      "-s", sessionName, command,
    );

    const capture = await captureWhenReady("TAIL-LEFT");
    const expected = capture.split("\n");
    const terminal = new Terminal({ ...MIRROR_TERMINAL_OPTIONS, cols, rows });
    await new Promise<void>((resolve) => terminal.write(`\u001b[H\u001b[J${capture}`, resolve));
    const actual = expected.map((_, row) =>
      terminal.buffer.active.getLine(row)?.translateToString(true) ?? "",
    );

    expect(actual).toEqual(expected);
    terminal.dispose();
  });
});
