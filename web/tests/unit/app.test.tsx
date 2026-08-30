import { render } from "preact";
import { act } from "preact/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../../src/app";
import { crc32, memberColor, minuteGroup, relativeActivity } from "../../src/format";
import { bootstrapFixture, taskDetailFixture, vocabularyFixture } from "../fixtures";

function mount() {
  const root = document.createElement("div");
  document.body.append(root);
  act(() => {
    render(
      <App
        initialBootstrap={bootstrapFixture}
        initialVocabulary={vocabularyFixture}
        initialTaskDetail={taskDetailFixture}
        initialRoute={{ view: "task", taskId: "T-014" }}
        pollMs={0}
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
  act(() => render(null, document.body.firstElementChild as Element));
  document.body.innerHTML = "";
  vi.restoreAllMocks();
});

describe("snapshot console", () => {
  it("renders task, members, evidence and immutable events from snapshots", () => {
    const root = mount();

    expect(root.querySelector("h2")?.textContent).toContain("WEB-005");
    expect(root.textContent).toContain("Fable Core");
    expect(root.textContent).toContain("tests/baseline/web-005-task-board-1440x1000.png");
    expect(root.textContent).toContain("不可覆盖事件流");
    expect(root.textContent).toContain("关联沟通");
    expect(root.querySelectorAll(".member-card")).toHaveLength(4);
    expect(root.querySelectorAll(".event-stream li")).toHaveLength(4);
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

    const control = [...root.querySelectorAll<HTMLButtonElement>(".filter-bar button")]
      .find((button) => button.textContent?.includes("控制事件"));
    await act(async () => control?.click());
    expect(root.querySelectorAll(".timeline-entry")).toHaveLength(1);
    expect(root.textContent).toContain("tmux session missing");
  });

  it("supports keyboard help, theme, and safe local exit", async () => {
    const root = mount();
    await act(async () => window.dispatchEvent(new KeyboardEvent("keydown", { key: "?" })));
    expect(root.querySelector("h2")?.textContent).toBe("快捷导航");

    await act(async () => window.dispatchEvent(new KeyboardEvent("keydown", { key: "t" })));
    expect(document.documentElement.dataset.theme).toBe("light");

    const exit = [...root.querySelectorAll<HTMLButtonElement>("button")]
      .find((button) => button.textContent === "退出");
    await act(async () => exit?.click());
    expect(root.textContent).toContain("成员与任务继续运行");
  });
});

describe("presentation protocol rules", () => {
  it("uses stable crc32 colors and raw ts minute groups", () => {
    expect(crc32("sol")).toBe(crc32("sol"));
    expect(memberColor("sol")).toBe(memberColor("sol"));
    expect(memberColor("sol")).toMatch(/^var\(--member-[0-7]\)$/);
    expect(minuteGroup("2026-08-30T18:44:59Z")).toBe("2026-08-30 18:44");
  });

  it("derives relative activity only from silent_for plus snapshot age", () => {
    expect(relativeActivity(12, 1_000, 1_048_000)).toBe("1 分前");
    expect(relativeActivity(null, 1_000, 9_000_000)).toBe("暂无输出");
  });
});
