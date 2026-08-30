import { render } from "preact";
import { act } from "preact/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../../src/app";
import { crc32, memberColor, minuteGroup, relativeActivity } from "../../src/format";
import { bootstrapFixture, taskDetailFixture, vocabularyFixture } from "../fixtures";

function mount() {
  const root = document.createElement("div");
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    const body = path.includes("/timeline") ? bootstrapFixture.timeline : taskDetailFixture;
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as unknown as typeof fetch;
  document.body.append(root);
  act(() => {
    render(
      <App
        initialBootstrap={bootstrapFixture}
        initialVocabulary={vocabularyFixture}
        initialTaskDetail={taskDetailFixture}
        initialRoute={{ view: "task", taskId: "T-016" }}
        pollMs={0}
        fetcher={fetcher}
      />,
      root,
    );
  });
  return root;
}

beforeEach(() => {
  localStorage.clear();
  window.history.replaceState(null, "", "/");
});

afterEach(() => {
  const root = document.body.firstElementChild;
  if (root) act(() => render(null, root));
  document.body.innerHTML = "";
  vi.restoreAllMocks();
});

describe("snapshot console", () => {
  it("renders task, members, evidence and immutable events from snapshots", () => {
    const root = mount();

    expect(root.querySelector("h2")?.textContent).toContain("WEB-006");
    expect(root.textContent).toContain("任务态势");
    expect(root.textContent).toContain("tests/baseline/web-006-compose-1440x1000.png");
    expect(root.textContent).toContain("不可覆盖事件流");
    expect(root.textContent).toContain("关联沟通");
    expect(root.querySelectorAll(".member-card")).toHaveLength(4);
    expect(root.querySelectorAll(".event-stream li")).toHaveLength(4);
    expect(root.querySelector('[aria-label="工作对话输入"]')).not.toBeNull();
    expect(root.textContent).toContain("关联 T-016");
  });

  it("navigates to timeline, filters categories, and clears client-local unread", async () => {
    localStorage.setItem("amux.web.last-seen", JSON.stringify({ epoch: bootstrapFixture.epoch, seq: 38 }));
    const root = mount();
    expect(root.querySelector(".unread-badge")?.textContent).toBe("3");

    const conversation = root.querySelector(".conversation-card") as HTMLButtonElement;
    await act(async () => conversation.click());
    expect(root.querySelector("h2")?.textContent).toBe("工作对话时间线");
    expect(window.location.pathname).toBe("/timeline");
    expect(root.querySelector(".unread-badge")?.textContent).toBe("0");
    const reply = [...root.querySelectorAll<HTMLButtonElement>("button")]
      .find((button) => button.textContent === "回复 ask");
    await act(async () => reply?.click());
    expect(root.textContent).toContain("回复 ask · msg-37");

    const control = [...root.querySelectorAll<HTMLButtonElement>(".filter-bar button")]
      .find((button) => button.textContent?.includes("控制事件"));
    await act(async () => {
      control?.click();
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });
    expect(root.querySelectorAll(".timeline-entry")).toHaveLength(1);
    expect(root.textContent).toContain("tmux session missing");
  });

  it("resets last_seen_seq to the new head when the stored epoch is stale", () => {
    localStorage.setItem("amux.web.last-seen", JSON.stringify({ epoch: "old-epoch", seq: 1 }));
    const root = mount();

    expect(root.querySelector(".unread-badge")?.textContent).toBe("0");
    expect(JSON.parse(localStorage.getItem("amux.web.last-seen") ?? "{}")).toEqual({
      epoch: bootstrapFixture.epoch,
      seq: bootstrapFixture.timeline.head_seq,
    });
  });

  it("supports keyboard help, theme, and safe local exit", async () => {
    const root = mount();
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "?" }));
    });
    expect(root.querySelector("h2")?.textContent).toBe("快捷导航");

    await act(async () => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "t" }));
    });
    expect(document.documentElement.dataset.theme).toBe("light");

    const exit = [...root.querySelectorAll<HTMLButtonElement>("button")]
      .find((button) => button.textContent === "退出");
    await act(async () => exit?.click());
    expect(root.textContent).toContain("成员与任务继续运行");
  });
});

describe("presentation protocol rules", () => {
  it("uses stable crc32 colors and raw ts minute groups", () => {
    expect(crc32("sol")).toBe(261575936);
    expect(memberColor("sol")).toBe(memberColor("sol"));
    expect(memberColor("sol")).toMatch(/^var\(--member-[0-7]\)$/);
    expect(minuteGroup("2026-08-30T18:44:59Z")).toBe("2026-08-30 18:44");
  });

  it("derives relative activity only from silent_for plus snapshot age", () => {
    expect(relativeActivity(12, 1_000, 1_048_000)).toBe("1 分前");
    expect(relativeActivity(null, 1_000, 9_000_000)).toBe("暂无输出");
  });
});
