import type { RevisionMap, TimelineEntry, TimelineSnapshot } from "./model";

export type StreamDomain = "workspace" | "team" | "roster" | "work" | "timeline" | "member" | "health";
export type StreamStatus = "connecting" | "connected" | "reconnecting" | "offline";

export interface StreamFrame {
  type: "hello" | "invalidation" | "delta" | "resync" | "epoch_changed" | "ping" | "error";
  epoch: string;
  revisions?: RevisionMap;
  domain?: StreamDomain | "*";
  revision?: number;
  reason?: string;
  ops?: Array<Record<string, unknown>>;
}

interface StreamState {
  epoch: string;
  revisions: RevisionMap;
}

interface StreamHandlers {
  current: () => StreamState;
  resync: (domain: StreamDomain | "*") => Promise<StreamState>;
  delta: (frame: StreamFrame) => Promise<StreamState | void> | StreamState | void;
  status: (status: StreamStatus) => void;
}

interface WebSocketLike {
  onopen: ((event: Event) => void) | null;
  onmessage: ((event: MessageEvent<string>) => void) | null;
  onclose: ((event: CloseEvent) => void) | null;
  onerror: ((event: Event) => void) | null;
  readonly readyState: number;
  send(data: string): void;
  close(code?: number): void;
}

type SocketFactory = (url: string) => WebSocketLike;

const DOMAINS: StreamDomain[] = [
  "workspace", "team", "roster", "work", "timeline", "member", "health",
];
const SOCKET_OPEN = 1;

function defaultSocketFactory(url: string): WebSocketLike {
  return new WebSocket(url);
}

function streamUrl(): string {
  const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${window.location.host}/api/v1/stream`;
}

export function connectEventStream(
  handlers: StreamHandlers,
  socketFactory: SocketFactory = defaultSocketFactory,
): () => void {
  let stopped = false;
  let socket: WebSocketLike | null = null;
  let reconnectTimer: number | undefined;
  let attempt = 0;
  let known: RevisionMap = { ...handlers.current().revisions };
  let queue = Promise.resolve();

  const send = (frame: Record<string, unknown>) => {
    if (socket?.readyState === SOCKET_OPEN) socket.send(JSON.stringify(frame));
  };

  const subscribe = (state: StreamState) => {
    known = { ...state.revisions };
    send({ type: "subscribe", epoch: state.epoch, domains: DOMAINS, known });
  };

  const fullResync = async () => {
    const state = await handlers.resync("*");
    subscribe(state);
  };

  const handle = async (frame: StreamFrame) => {
    if (frame.type === "ping") {
      send({ type: "pong" });
      return;
    }
    if (frame.type === "hello") {
      const state = handlers.current();
      if (frame.epoch !== state.epoch) await fullResync();
      else subscribe(state);
      attempt = 0;
      handlers.status("connected");
      return;
    }
    if (frame.type === "epoch_changed" || frame.epoch !== handlers.current().epoch) {
      await fullResync();
      return;
    }
    if (frame.type === "resync" || frame.type === "invalidation") {
      const state = await handlers.resync(frame.domain ?? "*");
      known = { ...state.revisions };
      return;
    }
    if (frame.type !== "delta" || !frame.domain || frame.domain === "*") return;
    const expected = (known[frame.domain] ?? 0) + 1;
    if (frame.revision !== expected) {
      const state = await handlers.resync(frame.domain);
      known = { ...state.revisions };
      return;
    }
    const state = await handlers.delta(frame);
    if (state) known = { ...state.revisions };
    else known[frame.domain] = frame.revision;
  };

  const connect = () => {
    if (stopped) return;
    handlers.status(attempt ? "reconnecting" : "connecting");
    socket = socketFactory(streamUrl());
    socket.onmessage = (event) => {
      queue = queue.catch(() => undefined).then(async () => {
        try {
          await handle(JSON.parse(event.data) as StreamFrame);
        } catch {
          try {
            await fullResync();
          } catch {
            socket?.close();
          }
        }
      });
    };
    socket.onerror = () => socket?.close();
    socket.onclose = (event) => {
      if (stopped) return;
      if (event.code === 1008) {
        handlers.status("offline");
        return;
      }
      handlers.status("reconnecting");
      const delay = Math.min(30_000, 1000 * 2 ** attempt) * (0.85 + Math.random() * 0.3);
      attempt += 1;
      reconnectTimer = window.setTimeout(connect, delay);
    };
  };

  connect();
  return () => {
    stopped = true;
    if (reconnectTimer !== undefined) window.clearTimeout(reconnectTimer);
    socket?.close(1000);
  };
}

export function applyTimelineDelta(
  timeline: TimelineSnapshot,
  frame: StreamFrame,
): TimelineSnapshot {
  const bySeq = new Map(timeline.entries.map((entry) => [entry.seq, entry]));
  const counts = { ...timeline.category_counts };
  for (const operation of frame.ops ?? []) {
    if (operation.op === "append" && operation.entry) {
      const entry = operation.entry as unknown as TimelineEntry;
      if (!bySeq.has(entry.seq)) {
        counts.all += 1;
        counts[entry.category] += 1;
      }
      bySeq.set(entry.seq, entry);
    } else if (operation.op === "update" && typeof operation.seq === "number") {
      const previous = bySeq.get(operation.seq);
      if (previous) {
        bySeq.set(operation.seq, {
          ...previous,
          outcome: typeof operation.outcome === "string" ? operation.outcome : previous.outcome,
          reason: typeof operation.reason === "string" ? operation.reason : previous.reason,
        });
      }
    }
  }
  const entries = [...bySeq.values()].sort(
    (left, right) => left.at - right.at || left.key.localeCompare(right.key),
  );
  const seqs = entries.map((entry) => entry.seq);
  return {
    ...timeline,
    revision: frame.revision ?? timeline.revision,
    entries,
    category_counts: counts,
    head_seq: seqs.length ? Math.max(timeline.head_seq, ...seqs) : timeline.head_seq,
    oldest_seq: seqs.length ? Math.min(...seqs) : timeline.oldest_seq,
  };
}
