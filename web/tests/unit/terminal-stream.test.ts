import { describe, expect, it, vi } from "vitest";
import {
  connectTerminalAttach,
  connectTerminalMirror,
  directInputMessageForKeyEvent,
  leaseAcquireMessage,
  type TerminalServerMessage,
} from "../../src/terminal-stream";

class MockSocket {
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  readyState = 1;
  sent: Array<Record<string, unknown>> = [];

  send(data: string) {
    this.sent.push(JSON.parse(data) as Record<string, unknown>);
  }

  close() {
    this.readyState = 3;
  }

  emit(message: TerminalServerMessage) {
    this.onmessage?.(new MessageEvent("message", { data: JSON.stringify(message) }));
  }
}

const flush = () => new Promise((resolve) => window.setTimeout(resolve, 0));

describe("terminal mirror WebSocket client", () => {
  it("relays server messages and lets the caller send protocol frames", () => {
    const socket = new MockSocket();
    const message = vi.fn();
    const status = vi.fn();
    const connection = connectTerminalMirror("claude", { message, status }, () => socket);

    socket.onopen?.(new Event("open"));
    expect(status).toHaveBeenCalledWith("connected");

    const frame: TerminalServerMessage = {
      type: "frame",
      member: "claude",
      frame_seq: 1,
      cols: 80,
      rows: 24,
      history_offset: 0,
      captured_at: 0,
      cursor_y: 0,
      input_rows: [3],
      live_allowed: true,
      encoding: "ansi",
      data: "hello",
    };
    socket.emit(frame);
    expect(message).toHaveBeenCalledWith(frame);

    connection.send(leaseAcquireMessage(false, "direct-ticket"));
    expect(socket.sent).toEqual([{
      type: "lease",
      action: "acquire",
      force: false,
      direct_token: "direct-ticket",
    }]);

    connection.disconnect();
    expect(socket.readyState).toBe(3);
  });

  it("does not reconnect after a server-initiated rejection close (code >= 4000)", async () => {
    const socket = new MockSocket();
    const status = vi.fn();
    const rejected = vi.fn();
    let factoryCalls = 0;
    connectTerminalMirror("claude", { message: vi.fn(), status, rejected }, () => {
      factoryCalls += 1;
      return socket;
    });
    expect(factoryCalls).toBe(1);

    // 模拟服务端 accept() 后立即用 4401 关闭(src/web/app.py 的 reject())。
    socket.onclose?.({ code: 4401, reason: "unauthorized" } as CloseEvent);
    await flush();

    expect(status).toHaveBeenCalledWith("offline");
    expect(rejected).toHaveBeenCalledWith(4401, "unauthorized");
    expect(factoryCalls).toBe(1); // 没有重连
  });

  it("reconnects with backoff after a non-rejection close", async () => {
    vi.useFakeTimers();
    try {
      let factoryCalls = 0;
      const sockets: MockSocket[] = [];
      connectTerminalMirror("claude", { message: vi.fn(), status: vi.fn() }, () => {
        factoryCalls += 1;
        const socket = new MockSocket();
        sockets.push(socket);
        return socket;
      });
      expect(factoryCalls).toBe(1);

      sockets[0].onclose?.({ code: 1006, reason: "" } as CloseEvent);
      await vi.advanceTimersByTimeAsync(2000);
      expect(factoryCalls).toBe(2);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("directInputMessageForKeyEvent", () => {
  const key = (init: Partial<KeyboardEventInit> & { key: string }) =>
    new KeyboardEvent("keydown", init);

  it("maps the documented whitelist and printable characters", () => {
    expect(directInputMessageForKeyEvent(key({ key: "Enter" }))).toEqual({
      type: "input",
      kind: "submit",
    });
    expect(directInputMessageForKeyEvent(key({ key: "Tab" }))).toEqual({
      type: "input",
      kind: "key",
      name: "Tab",
    });
    expect(directInputMessageForKeyEvent(key({ key: "Tab", shiftKey: true }))).toEqual({
      type: "input",
      kind: "key",
      name: "BTab",
    });
    expect(directInputMessageForKeyEvent(key({ key: "Backspace" }))).toEqual({
      type: "input",
      kind: "key",
      name: "BSpace",
    });
    expect(directInputMessageForKeyEvent(key({ key: "ArrowUp" }))).toEqual({
      type: "input",
      kind: "key",
      name: "Up",
    });
    expect(directInputMessageForKeyEvent(key({ key: "a" }))).toEqual({
      type: "input",
      kind: "text",
      data: "a",
    });
  });

  it("ignores Escape and modifier-held keys", () => {
    expect(directInputMessageForKeyEvent(key({ key: "Escape" }))).toBeNull();
    expect(directInputMessageForKeyEvent(key({ key: "c", ctrlKey: true }))).toBeNull();
    expect(directInputMessageForKeyEvent(key({ key: "a", metaKey: true }))).toBeNull();
  });
});

describe("terminal attach WebSocket client", () => {
  it("puts the one-time ticket in the first frame, never in the URL", () => {
    let openedUrl = "";
    const sent: Array<string | ArrayBufferView> = [];
    const socket = {
      binaryType: "blob" as BinaryType,
      onopen: null as ((event: Event) => void) | null,
      onmessage: null as ((event: MessageEvent<unknown>) => void) | null,
      onclose: null as ((event: CloseEvent) => void) | null,
      onerror: null as ((event: Event) => void) | null,
      readyState: 1,
      send: (data: string | ArrayBufferView) => sent.push(data),
      close: vi.fn(),
    };
    const data = vi.fn();
    connectTerminalAttach(
      "claude",
      { attach_token: "single-use-ticket", force: false, cols: 100, rows: 30 },
      { status: vi.fn(), data, control: vi.fn() },
      (url) => {
        openedUrl = url;
        return socket;
      },
    );

    socket.onopen?.(new Event("open"));
    expect(openedUrl).not.toContain("single-use-ticket");
    expect(JSON.parse(sent[0] as string)).toEqual({
      type: "attach",
      attach_token: "single-use-ticket",
      force: false,
      cols: 100,
      rows: 30,
    });

    const bytes = new Uint8Array([27, 91, 72]).buffer;
    socket.onmessage?.(new MessageEvent("message", { data: bytes }));
    expect(data).toHaveBeenCalledWith(new Uint8Array(bytes));
  });
});
