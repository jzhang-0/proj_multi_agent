import { describe, expect, it, vi } from "vitest";
import { applyTimelineDelta, connectEventStream, type StreamFrame } from "../../src/stream";
import { bootstrapFixture } from "../fixtures";

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

  emit(frame: StreamFrame) {
    this.onmessage?.(new MessageEvent("message", { data: JSON.stringify(frame) }));
  }
}

const flush = () => new Promise((resolve) => window.setTimeout(resolve, 0));

describe("versioned event stream", () => {
  it("subscribes from known revisions, applies contiguous deltas and resyncs gaps", async () => {
    const socket = new MockSocket();
    const resync = vi.fn(async () => ({
      epoch: bootstrapFixture.epoch,
      revisions: { ...bootstrapFixture.revisions, timeline: 45 },
    }));
    const delta = vi.fn();
    const disconnect = connectEventStream(
      {
        current: () => ({ epoch: bootstrapFixture.epoch, revisions: bootstrapFixture.revisions }),
        resync,
        delta,
        status: vi.fn(),
      },
      () => socket,
    );

    socket.emit({
      type: "hello",
      epoch: bootstrapFixture.epoch,
      revisions: bootstrapFixture.revisions,
    });
    await flush();
    expect(socket.sent[0]).toMatchObject({
      type: "subscribe",
      epoch: bootstrapFixture.epoch,
      known: bootstrapFixture.revisions,
    });

    socket.emit({
      type: "delta",
      epoch: bootstrapFixture.epoch,
      domain: "timeline",
      revision: bootstrapFixture.revisions.timeline + 1,
      ops: [],
    });
    await flush();
    expect(delta).toHaveBeenCalledOnce();

    socket.emit({
      type: "delta",
      epoch: bootstrapFixture.epoch,
      domain: "timeline",
      revision: bootstrapFixture.revisions.timeline + 3,
      ops: [],
    });
    await flush();
    expect(resync).toHaveBeenCalledWith("timeline");

    socket.emit({ type: "ping", epoch: bootstrapFixture.epoch });
    await flush();
    expect(socket.sent.at(-1)).toEqual({ type: "pong" });
    disconnect();
  });

  it("resets all domains when the server epoch changes", async () => {
    const socket = new MockSocket();
    const resync = vi.fn(async () => ({ epoch: "new-epoch", revisions: { timeline: 0 } }));
    const disconnect = connectEventStream(
      {
        current: () => ({ epoch: bootstrapFixture.epoch, revisions: bootstrapFixture.revisions }),
        resync,
        delta: vi.fn(),
        status: vi.fn(),
      },
      () => socket,
    );
    socket.emit({ type: "hello", epoch: "new-epoch", revisions: { timeline: 0 } });
    await flush();
    expect(resync).toHaveBeenCalledWith("*");
    expect(socket.sent.at(-1)).toMatchObject({ type: "subscribe", epoch: "new-epoch" });
    disconnect();
  });

  it("applies append/update ops without duplicating stable seq entries", () => {
    const timeline = bootstrapFixture.timeline;
    const appended = {
      ...timeline.entries.at(-1)!,
      seq: 42,
      key: "msg-42",
      at: timeline.entries[0].at - 1,
      category: "ai" as const,
      outcome: "pending",
    };
    const next = applyTimelineDelta(timeline, {
      type: "delta",
      epoch: timeline.epoch,
      domain: "timeline",
      revision: timeline.revision + 1,
      ops: [{ op: "append", entry: appended }],
    });
    const updated = applyTimelineDelta(next, {
      type: "delta",
      epoch: timeline.epoch,
      domain: "timeline",
      revision: timeline.revision + 2,
      ops: [{ op: "update", seq: 42, outcome: "delivered", reason: "" }],
    });

    expect(updated.entries.filter((entry) => entry.seq === 42)).toHaveLength(1);
    expect(updated.entries.find((entry) => entry.seq === 42)?.outcome).toBe("delivered");
    expect(updated.entries[0].seq).toBe(42);
    expect(updated.head_seq).toBe(42);
    expect(updated.category_counts.all).toBe(timeline.category_counts.all + 1);
  });
});
