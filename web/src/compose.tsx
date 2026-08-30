import { useEffect, useMemo, useRef, useState } from "preact/hooks";

import { sendMessage, uploadAttachment, type Fetcher } from "./api";
import type { AttachmentUpload, BootstrapSnapshot, TimelineEntry } from "./model";

const LAST_TARGET_KEY = "amux.web.last-target";

export interface ReplyContext {
  id: string;
  sender: string;
  text: string;
}

interface PendingAttachment {
  localId: string;
  name: string;
  previewUrl: string;
  state: "uploading" | "ready" | "failed";
  remote?: AttachmentUpload;
  error?: string;
}

export interface ComposeBarProps {
  snapshot: BootstrapSnapshot;
  taskId: string | null;
  preferLeader: boolean;
  reply: ReplyContext | null;
  onReplyChange: (reply: ReplyContext | null) => void;
  fetcher?: Fetcher;
}

function uniqueMembers(snapshot: BootstrapSnapshot): string[] {
  const names = [
    ...(snapshot.team.leader ? [snapshot.team.leader] : []),
    ...(snapshot.team.members?.map((member) => member.id) ?? []),
    ...snapshot.members.members.map((member) => member.name),
  ];
  return [...new Set(names)].sort((left, right) => left.localeCompare(right));
}

function addressedValue(value: string): { target?: string; text: string } {
  const matched = value.match(/^@([a-z][a-z0-9-]*)(?:\s+([\s\S]*))?$/i);
  if (matched === null) return { text: value };
  return { target: matched[1], text: matched[2] ?? "" };
}

function localId(): string {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
}

export function ComposeBar({
  snapshot,
  taskId,
  preferLeader,
  reply,
  onReplyChange,
  fetcher = fetch,
}: ComposeBarProps) {
  const leader = snapshot.team.leader ?? "";
  const writeToken = snapshot.session.write_token;
  const members = useMemo(() => uniqueMembers(snapshot), [snapshot.members, snapshot.team]);
  const [mode, setMode] = useState<"message" | "ask">("message");
  const [value, setValue] = useState("");
  const [target, setTarget] = useState(() => {
    const remembered = localStorage.getItem(LAST_TARGET_KEY);
    return remembered !== null && members.includes(remembered) ? remembered : leader;
  });
  const [attachments, setAttachments] = useState<PendingAttachment[]>([]);
  const [candidateIndex, setCandidateIndex] = useState(0);
  const [sending, setSending] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const attachmentsRef = useRef(attachments);

  const candidateMatch = value.match(/^@([a-z0-9-]*)$/i);
  const candidates = candidateMatch === null
    ? []
    : members.filter((name) => name.startsWith(candidateMatch[1].toLowerCase()));
  const linkedTask = !reply && mode === "message" ? taskId : null;
  const activeTarget = reply?.sender ?? target ?? leader;
  const readyAttachments = attachments.filter((item) => item.state === "ready");
  const attachmentsBlocked = attachments.some((item) => item.state !== "ready");

  useEffect(() => {
    const remembered = localStorage.getItem(LAST_TARGET_KEY);
    const safeRemembered = remembered !== null && members.includes(remembered) ? remembered : leader;
    setTarget(preferLeader ? leader : safeRemembered);
  }, [leader, members, preferLeader]);

  useEffect(() => {
    if (reply !== null) inputRef.current?.focus({ preventScroll: true });
  }, [reply]);

  useEffect(() => {
    attachmentsRef.current = attachments;
  }, [attachments]);

  useEffect(() => () => {
    for (const attachment of attachmentsRef.current) URL.revokeObjectURL(attachment.previewUrl);
  }, []);

  function selectCandidate(name: string) {
    setTarget(name);
    setValue("");
    setCandidateIndex(0);
    setNotice(`收件人已切换为 ${name}`);
    inputRef.current?.focus({ preventScroll: true });
  }

  function removeAttachment(localAttachmentId: string) {
    setAttachments((current) => {
      const removed = current.find((item) => item.localId === localAttachmentId);
      if (removed) URL.revokeObjectURL(removed.previewUrl);
      return current.filter((item) => item.localId !== localAttachmentId);
    });
  }

  function removeLastAttachment() {
    const latest = attachments.at(-1);
    if (latest) removeAttachment(latest.localId);
  }

  async function addFiles(files: File[]) {
    if (writeToken === null) return;
    const remaining = Math.max(0, 8 - attachments.length);
    const accepted = files.filter((file) => file.type.startsWith("image/")).slice(0, remaining);
    if (accepted.length === 0) {
      setNotice(remaining === 0 ? "单条消息最多附加 8 张图片" : "剪贴板中没有图片");
      return;
    }
    for (const file of accepted) {
      const id = localId();
      const pending: PendingAttachment = {
        localId: id,
        name: file.name || "pasted-image",
        previewUrl: URL.createObjectURL(file),
        state: "uploading",
      };
      setAttachments((current) => [...current, pending]);
      try {
        const remote = await uploadAttachment(file, writeToken, fetcher);
        setAttachments((current) => current.map((item) => (
          item.localId === id ? { ...item, remote, state: "ready" } : item
        )));
        setNotice(`已附加 ${remote.width}×${remote.height} 图片`);
      } catch (caught) {
        const error = caught instanceof Error ? caught.message : String(caught);
        setAttachments((current) => current.map((item) => (
          item.localId === id ? { ...item, state: "failed", error } : item
        )));
        setNotice(error);
      }
    }
  }

  async function submit() {
    if (writeToken === null || sending || attachmentsBlocked) return;
    const addressed = addressedValue(value);
    const recipient = addressed.target ?? activeTarget;
    if (!addressed.text.trim() && readyAttachments.length === 0) {
      setNotice("请输入消息或附加图片");
      return;
    }
    setSending(true);
    setNotice(null);
    try {
      const receipt = await sendMessage({
        ...(reply
          ? { kind: "reply" as const, reply_to: reply.id }
          : { kind: mode, to: recipient || undefined }),
        text: addressed.text,
        ...(linkedTask ? { task_id: linkedTask } : {}),
        attachment_ids: readyAttachments.map((item) => item.remote!.id),
      }, writeToken, fetcher);
      localStorage.setItem(LAST_TARGET_KEY, receipt.to);
      for (const attachment of attachments) URL.revokeObjectURL(attachment.previewUrl);
      setAttachments([]);
      setValue("");
      setMode("message");
      onReplyChange(null);
      setTarget(preferLeader ? leader : receipt.to);
      const label = receipt.kind === "ask" ? "Ask 已发送" : receipt.kind === "reply" ? "回复已发送" : "消息已发送";
      setNotice(`${label}给 ${receipt.to}`);
      inputRef.current?.focus({ preventScroll: true });
    } catch (caught) {
      setNotice(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setSending(false);
    }
  }

  return (
    <section class="compose-bar" aria-label="工作对话输入">
      <div class="compose-context">
        <div class="compose-modes" role="group" aria-label="消息类型">
          <button
            class={!reply && mode === "message" ? "is-active" : ""}
            disabled={reply !== null}
            onClick={() => setMode("message")}
          >消息</button>
          <button
            class={!reply && mode === "ask" ? "is-active" : ""}
            disabled={reply !== null}
            onClick={() => setMode("ask")}
          >Ask</button>
        </div>
        <span class="compose-route">{reply ? `回复 ${reply.sender}` : `发送给 ${activeTarget || "—"}`}</span>
        {linkedTask ? <span class="compose-task">关联 {linkedTask}</span> : null}
        {!reply && mode === "ask" ? <span class="compose-rule">Ask 不关联任务</span> : null}
      </div>
      {reply ? (
        <div class="reply-context">
          <div><strong>回复 ask · {reply.id.slice(0, 10)}</strong><span>{reply.sender}: {reply.text}</span></div>
          <button aria-label="取消回复" onClick={() => onReplyChange(null)}>×</button>
        </div>
      ) : null}
      {attachments.length ? (
        <div class="pending-attachments" aria-label="待发图片">
          {attachments.map((attachment) => (
            <article class={`pending-attachment is-${attachment.state}`} key={attachment.localId}>
              <img src={attachment.previewUrl} alt="待发图片预览" />
              <span>
                <strong>{attachment.remote ? `${attachment.remote.width}×${attachment.remote.height}` : attachment.name}</strong>
                <small>{attachment.state === "uploading" ? "正在安全保存…" : attachment.state === "failed" ? attachment.error : attachment.remote?.id}</small>
              </span>
              <button aria-label={`撤销图片 ${attachment.name}`} onClick={() => removeAttachment(attachment.localId)}>×</button>
            </article>
          ))}
        </div>
      ) : null}
      <form class="compose-form" onSubmit={(event) => { event.preventDefault(); void submit(); }}>
        <button class="attach-button" type="button" title="添加图片" onClick={() => fileRef.current?.click()}>＋图片</button>
        <input
          class="visually-hidden"
          ref={fileRef}
          type="file"
          accept="image/*"
          multiple
          onChange={(event) => {
            void addFiles(Array.from(event.currentTarget.files ?? []));
            event.currentTarget.value = "";
          }}
        />
        <div class="compose-input-wrap">
          <textarea
            ref={inputRef}
            rows={2}
            value={value}
            disabled={writeToken === null}
            placeholder={writeToken === null ? "当前会话仅可查看" : `对 ${activeTarget || "Leader"} 说话；输入 @ 选择成员，粘贴图片可直接发送`}
            onInput={(event) => { setValue(event.currentTarget.value); setCandidateIndex(0); }}
            onPaste={(event) => {
              const files = Array.from(event.clipboardData?.files ?? [])
                .filter((file) => file.type.startsWith("image/"));
              if (files.length) {
                event.preventDefault();
                void addFiles(files);
              }
            }}
            onKeyDown={(event) => {
              if (candidates.length) {
                if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                  event.preventDefault();
                  const step = event.key === "ArrowDown" ? 1 : -1;
                  setCandidateIndex((current) => (current + step + candidates.length) % candidates.length);
                  return;
                }
                if (event.key === "Tab" || event.key === "Enter") {
                  event.preventDefault();
                  selectCandidate(candidates[candidateIndex] ?? candidates[0]);
                  return;
                }
              }
              if ((event.key === "Backspace" || event.key === "Delete") && value === "" && attachments.length) {
                event.preventDefault();
                removeLastAttachment();
                return;
              }
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void submit();
              }
            }}
          />
          {candidates.length ? (
            <div class="member-completions" role="listbox" aria-label="成员补全">
              {candidates.map((name, index) => (
                <button
                  class={index === candidateIndex ? "is-active" : ""}
                  type="button"
                  role="option"
                  aria-selected={index === candidateIndex}
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => selectCandidate(name)}
                  key={name}
                >@{name}</button>
              ))}
            </div>
          ) : null}
        </div>
        <button class="send-button" type="submit" disabled={sending || attachmentsBlocked || writeToken === null}>
          {sending ? "发送中…" : reply ? "回复" : mode === "ask" ? "发送 Ask" : "发送"}
        </button>
      </form>
      <div class="compose-foot">
        <span>Enter 发送 · Shift+Enter 换行 · 空输入 Backspace/Delete 撤销末张</span>
        <span class={notice?.includes("失败") ? "is-error" : ""} role="status">{notice ?? "图片仅以内容 id 暴露给浏览器"}</span>
      </div>
    </section>
  );
}

export function replyContext(entry: TimelineEntry): ReplyContext {
  return { id: entry.key, sender: entry.sender, text: entry.text };
}
