// WEB-007 §2-§7:镜像 + 直连输入 WebSocket 客户端。结构对齐 stream.ts(WEB-004)
// 的 callback-object 风格,但协议语义不同——这里没有 epoch/revision 一致性
// 校验,服务端每条消息自成一体,客户端只需按 type 分发。

export type TerminalStatus = "connecting" | "connected" | "reconnecting" | "offline";

export interface LeaseHolder {
  owner: string;
  host: string;
  acquired_at: number;
}

export interface MirrorFrame {
  type: "frame";
  member: string;
  frame_seq: number;
  //: §5:canonical size 由租约持有者决定；非持有者只能从帧里的这两个字段
  //: 知道权威尺寸，不能只信自己的本地测量(评审 opus 实测发现)。
  cols: number;
  rows: number;
  history_offset: number;
  captured_at: number;
  cursor_y: number;
  input_rows: number[];
  live_allowed: boolean;
  encoding: "ansi";
  data: string;
}

export interface IdleFrame {
  type: "idle";
  member: string;
  frame_seq: number;
}

export interface LeaseDeniedFrame {
  type: "lease_denied";
  holder: LeaseHolder;
}

export interface LeaseAcquiredFrame {
  type: "lease_acquired";
  holder: LeaseHolder;
  preempted?: boolean;
  previous_holder?: LeaseHolder;
}

export interface LeaseLostFrame {
  type: "lease_lost";
}

export interface DeniedFrame {
  type: "denied";
  reason: string;
}

export interface LiveFrame {
  type: "live";
  active: boolean;
}

export interface NoticeFrame {
  type: "notice";
  text: string;
}

export type TerminalServerMessage =
  | MirrorFrame
  | IdleFrame
  | LeaseDeniedFrame
  | LeaseAcquiredFrame
  | LeaseLostFrame
  | DeniedFrame
  | LiveFrame
  | NoticeFrame;

interface TerminalHandlers {
  message: (message: TerminalServerMessage) => void;
  status: (status: TerminalStatus) => void;
  /** 服务端在握手阶段就 close(code>=4000) 时触发(§ web/app.py WS_CLOSE_*);
   * 这类关闭不重连——原因通常是鉴权/成员不存在/工作区未就绪,重试无意义。 */
  rejected?: (code: number, reason: string) => void;
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

const SOCKET_OPEN = 1;
//: web/app.py 的 WS_CLOSE_* 私有关闭码段(RFC 6455 4000-4999);服务端已经
//: accept() 后才 close(),浏览器能看到这个码(见 src/web/app.py 的 reject())。
const REJECTION_CODE_FLOOR = 4000;

function defaultSocketFactory(url: string): WebSocketLike {
  return new WebSocket(url);
}

function terminalUrl(member: string): string {
  const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${window.location.host}/api/v1/terminal/${encodeURIComponent(member)}/mirror`;
}

export interface TerminalConnection {
  send: (message: Record<string, unknown>) => void;
  disconnect: () => void;
}

export function connectTerminalMirror(
  member: string,
  handlers: TerminalHandlers,
  socketFactory: SocketFactory = defaultSocketFactory,
): TerminalConnection {
  let stopped = false;
  let socket: WebSocketLike | null = null;
  let reconnectTimer: number | undefined;
  let attempt = 0;

  const send = (message: Record<string, unknown>) => {
    if (socket?.readyState === SOCKET_OPEN) socket.send(JSON.stringify(message));
  };

  const connect = () => {
    if (stopped) return;
    handlers.status(attempt ? "reconnecting" : "connecting");
    socket = socketFactory(terminalUrl(member));
    socket.onopen = () => {
      attempt = 0;
      handlers.status("connected");
    };
    socket.onmessage = (event) => {
      try {
        handlers.message(JSON.parse(event.data) as TerminalServerMessage);
      } catch {
        // 忽略解析失败的单条消息,不影响连接本身。
      }
    };
    socket.onerror = () => socket?.close();
    socket.onclose = (event) => {
      if (stopped) return;
      if (event.code >= REJECTION_CODE_FLOOR) {
        handlers.status("offline");
        handlers.rejected?.(event.code, event.reason);
        return;
      }
      handlers.status("reconnecting");
      const delay = Math.min(30_000, 1000 * 2 ** attempt) * (0.85 + Math.random() * 0.3);
      attempt += 1;
      reconnectTimer = window.setTimeout(connect, delay);
    };
  };

  connect();
  return {
    send,
    disconnect: () => {
      stopped = true;
      if (reconnectTimer !== undefined) window.clearTimeout(reconnectTimer);
      socket?.close(1000);
    },
  };
}

// 客户端 -> 服务端消息构造(docs/web/terminal-protocol.md §3.2/§4.5/§5/§6.2/§7.1)。

export function leaseAcquireMessage(force: boolean): Record<string, unknown> {
  return { type: "lease", action: "acquire", force };
}

export function scrollMessage(offset: number): Record<string, unknown> {
  return { type: "scroll", offset };
}

export function resizeMessage(cols: number, rows: number): Record<string, unknown> {
  return { type: "resize", cols, rows };
}

export function focusInputMessage(frameSeq: number, row: number): Record<string, unknown> {
  return { type: "focus_input", frame_seq: frameSeq, row };
}

export function inputTextMessage(data: string): Record<string, unknown> {
  return { type: "input", kind: "text", data };
}

export function inputKeyMessage(name: string): Record<string, unknown> {
  return { type: "input", kind: "key", name };
}

export function inputSubmitMessage(): Record<string, unknown> {
  return { type: "input", kind: "submit" };
}

//: §7.1 白名单,与服务端 KEY_WHITELIST(src/web/terminal.py)逐字对齐。
export const DIRECT_KEY_WHITELIST = new Set([
  "Enter", "Tab", "BTab", "BSpace", "DC", "Up", "Down", "Left", "Right",
]);

//: §7.3 浏览器按键 -> 结构化消息映射表,与 console.mirror.Mirror._on_key 对应。
export function directInputMessageForKeyEvent(event: KeyboardEvent): Record<string, unknown> | null {
  if (event.ctrlKey || event.metaKey || event.altKey) return null;
  if (event.key === "Enter") return inputSubmitMessage();
  if (event.key === "Tab") return inputKeyMessage(event.shiftKey ? "BTab" : "Tab");
  if (event.key === "Backspace") return inputKeyMessage("BSpace");
  if (event.key === "Delete") return inputKeyMessage("DC");
  if (event.key === "ArrowUp") return inputKeyMessage("Up");
  if (event.key === "ArrowDown") return inputKeyMessage("Down");
  if (event.key === "ArrowLeft") return inputKeyMessage("Left");
  if (event.key === "ArrowRight") return inputKeyMessage("Right");
  if (event.key === "Escape") return null; // 本地退出直连态,不发送(§7.3)
  if (event.key.length === 1) return inputTextMessage(event.key);
  return null;
}
