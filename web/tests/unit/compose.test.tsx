import { render } from "preact";
import { act } from "preact/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ComposeBar } from "../../src/compose";
import { bootstrapFixture } from "../fixtures";

function response(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function mount() {
  const root = document.createElement("div");
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/attachments")) {
      return response({
        attachment: {
          id: "0123456789abcdef",
          name: "clipboard-0123456789abcdef.png",
          media_type: "image/png",
          width: 24,
          height: 16,
          size: 100,
          download_url: "/api/v1/attachments/0123456789abcdef",
        },
      });
    }
    const request = JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>;
    return response({
      ok: true,
      message: {
        id: "sent-id",
        to: request.kind === "reply" ? "fable" : request.to ?? "fable",
        kind: request.kind ?? "message",
        reply_to: request.reply_to ?? null,
        task_id: request.task_id ?? null,
        attachment_ids: request.attachment_ids ?? [],
      },
    });
  }) as unknown as typeof fetch;
  document.body.append(root);
  act(() => {
    render(
      <ComposeBar
        snapshot={bootstrapFixture}
        taskId="T-016"
        preferLeader
        reply={null}
        onReplyChange={vi.fn()}
        fetcher={fetcher}
      />,
      root,
    );
  });
  return { root, fetcher: fetcher as unknown as ReturnType<typeof vi.fn> };
}

function setText(textarea: HTMLTextAreaElement, value: string) {
  act(() => {
    textarea.value = value;
    textarea.dispatchEvent(new InputEvent("input", { bubbles: true }));
  });
}

beforeEach(() => {
  localStorage.clear();
  vi.stubGlobal("URL", {
    ...URL,
    createObjectURL: vi.fn(() => "blob:preview"),
    revokeObjectURL: vi.fn(),
  });
});

afterEach(() => {
  const root = document.body.firstElementChild;
  if (root) act(() => render(null, root));
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("work conversation composer", () => {
  it("defaults to Leader, links the selected task and never posts actor", async () => {
    const { root, fetcher } = mount();
    const textarea = root.querySelector("textarea")!;
    setText(textarea, "请验收浏览器消息闭环");

    await act(async () => {
      (root.querySelector(".send-button") as HTMLButtonElement).click();
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });

    const [, init] = fetcher.mock.calls.find(([path]) => String(path).endsWith("/messages"))!;
    const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
    expect(body).toMatchObject({
      kind: "message",
      to: "fable",
      task_id: "T-016",
    });
    expect(body).not.toHaveProperty("actor");
    expect(body).not.toHaveProperty("from");
    expect(init?.headers).toMatchObject({ "X-Amux-Session": "fixture-write-token" });
    expect(root.textContent).toContain("消息已发送给 fable");
  });

  it("ignores a remembered target that is no longer in the bound team", () => {
    localStorage.setItem("amux.web.last-target", "departed-member");
    const { root } = mount();

    expect(root.textContent).toContain("发送给 fable");
  });

  it("uses @ completion for ask and clears task association", async () => {
    const { root, fetcher } = mount();
    const textarea = root.querySelector("textarea")!;
    act(() => {
      (root.querySelectorAll(".compose-modes button")[1] as HTMLButtonElement).click();
    });
    setText(textarea, "@so");
    expect(root.querySelectorAll('[role="option"]')).toHaveLength(1);
    act(() => {
      textarea.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", bubbles: true }));
    });
    expect(root.textContent).toContain("发送给 sol");
    setText(textarea, "可以独立复核吗？");

    await act(async () => {
      (root.querySelector(".send-button") as HTMLButtonElement).click();
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });

    const [, init] = fetcher.mock.calls.find(([path]) => String(path).endsWith("/messages"))!;
    const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
    expect(body).toMatchObject({ kind: "ask", to: "sol" });
    expect(body).not.toHaveProperty("task_id");
  });

  it("uploads pasted images and empty Backspace only removes the pending reference", async () => {
    const { root, fetcher } = mount();
    const picker = root.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File([new Uint8Array([1, 2, 3])], "proof.png", { type: "image/png" });
    Object.defineProperty(picker, "files", { configurable: true, value: [file] });

    await act(async () => {
      picker.dispatchEvent(new Event("change", { bubbles: true }));
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });
    expect(root.querySelectorAll(".pending-attachment")).toHaveLength(1);
    expect(root.textContent).toContain("0123456789abcdef");

    const textarea = root.querySelector("textarea")!;
    act(() => {
      textarea.dispatchEvent(new KeyboardEvent("keydown", { key: "Backspace", bubbles: true }));
    });
    expect(root.querySelectorAll(".pending-attachment")).toHaveLength(0);
    expect(fetcher.mock.calls.filter(([path]) => String(path).endsWith("/attachments"))).toHaveLength(1);
    expect(fetcher.mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(false);
  });
});
