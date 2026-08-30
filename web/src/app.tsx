import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import {
  fetchMemberManagement,
  fetchBootstrap,
  fetchTaskDetail,
  fetchTimeline,
  fetchVocabulary,
  memberControl,
  type Fetcher,
} from "./api";
import { ComposeBar, replyContext, type ReplyContext } from "./compose";
import { formatTime, memberColor, minuteGroup, relativeActivity, vocabularyItem } from "./format";
import { applyTimelineDelta, connectEventStream, type StreamStatus } from "./stream";
import {
  connectTerminalMirror,
  connectTerminalAttach,
  directInputMessageForKeyEvent,
  focusInputMessage,
  leaseAcquireMessage,
  scrollMessage,
  type LeaseHolder,
  type TerminalConnection,
  type TerminalStatus,
  type AttachConnection,
} from "./terminal-stream";
import type {
  BootstrapSnapshot,
  Fault,
  MemberManagementSnapshot,
  RouteState,
  TaskDetailSnapshot,
  TimelineCategory,
  TimelineEntry,
  TimelineSnapshot,
  VocabularySnapshot,
} from "./model";
import type { Terminal as XTerm } from "@xterm/xterm";

//: §10:不引 addon-fit,用隐藏等宽测量元素算 cell 宽高——桌面浏览器字体渲染
//: 一致,这个近似值在真实终端里够用(见 docs/web/terminal-protocol.md §13
//: 待验证项,后续如需更精确可以换 addon-fit)。
const TERMINAL_FONT_FAMILY = "ui-monospace, SFMono-Regular, Menlo, monospace";
const TERMINAL_FONT_SIZE = 13;
const TERMINAL_LINE_HEIGHT = 1.2;
//: 对齐服务端 src/web/terminal.py 的 MIN_FIT_SIZE，客户端先做一次同样的
//: 下限裁剪，避免发送服务端注定会忽略的过小尺寸。
const MIN_FIT_COLS = 60;
const MIN_FIT_ROWS = 15;
const ROLLBACK_STEP = 20;

function measureCell(): { width: number; height: number } {
  const probe = document.createElement("span");
  probe.style.cssText = "position:absolute;visibility:hidden;top:-9999px;left:-9999px;white-space:pre;";
  probe.style.fontFamily = TERMINAL_FONT_FAMILY;
  probe.style.fontSize = `${TERMINAL_FONT_SIZE}px`;
  probe.textContent = "X".repeat(32);
  document.body.appendChild(probe);
  const rect = probe.getBoundingClientRect();
  document.body.removeChild(probe);
  return { width: rect.width / 32, height: TERMINAL_FONT_SIZE * TERMINAL_LINE_HEIGHT };
}

function ensureXtermStylesheet(): void {
  if (document.querySelector("link[data-xterm-css]")) return;
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = "/assets/xterm.css";
  link.dataset.xtermCss = "true";
  document.head.appendChild(link);
}

const CATEGORY_ORDER: TimelineCategory[] = ["all", "human", "ai", "task", "control"];
const LAST_SEEN_KEY = "amux.web.last-seen";
const THEME_KEY = "amux.web.theme";

interface AppProps {
  initialBootstrap?: BootstrapSnapshot;
  initialVocabulary?: VocabularySnapshot;
  initialTaskDetail?: TaskDetailSnapshot;
  initialRoute?: RouteState;
  pollMs?: number;
  fetcher?: Fetcher;
}

function routeFromPath(pathname = window.location.pathname): RouteState {
  const taskMatch = pathname.match(/^\/task\/([^/]+)$/);
  if (taskMatch) return { view: "task", taskId: decodeURIComponent(taskMatch[1]) };
  const memberMatch = pathname.match(/^\/member\/([^/]+)\/terminal$/);
  if (memberMatch) return { view: "terminal", member: decodeURIComponent(memberMatch[1]) };
  if (pathname === "/timeline") return { view: "timeline" };
  if (pathname === "/workspace") return { view: "workspace" };
  if (pathname === "/help") return { view: "help" };
  return { view: "task", taskId: null };
}

function routePath(route: RouteState): string {
  if (route.view === "task") return route.taskId ? `/task/${encodeURIComponent(route.taskId)}` : "/";
  if (route.view === "terminal") return `/member/${encodeURIComponent(route.member)}/terminal`;
  return `/${route.view}`;
}

function readLastSeen(): { epoch: string; seq: number } | null {
  try {
    return JSON.parse(localStorage.getItem(LAST_SEEN_KEY) ?? "null") as {
      epoch: string;
      seq: number;
    } | null;
  } catch {
    return null;
  }
}

function writeLastSeen(epoch: string, seq: number): void {
  localStorage.setItem(LAST_SEEN_KEY, JSON.stringify({ epoch, seq }));
}

function onNextFrame(callback: () => void): void {
  if (window.requestAnimationFrame) window.requestAnimationFrame(callback);
  else window.setTimeout(callback, 0);
}

function Glyph({ value }: { value: string }) {
  return <span aria-hidden="true">{value}</span>;
}

function StatusPill({ value, vocabulary }: { value: string; vocabulary?: VocabularySnapshot }) {
  const item = vocabularyItem(vocabulary?.task_status, value);
  return (
    <span class={`status-pill status-pill--${value}`}>
      {item.glyph ? <Glyph value={item.glyph} /> : null} {item.label}
    </span>
  );
}

function HealthBanner({ snapshot }: { snapshot: BootstrapSnapshot }) {
  if (!snapshot.health.degraded) return null;
  return (
    <aside class="health-banner" role="alert">
      <span class="health-banner__icon">!</span>
      <div>
        <strong>运行状态降级</strong>
        {snapshot.health.faults.map((fault) => (
          <p key={fault.key}>{fault.target ? `${fault.target} · ` : ""}{fault.detail}</p>
        ))}
      </div>
    </aside>
  );
}

function Sidebar({
  snapshot,
  vocabulary,
  selectedTask,
  unread,
  nowMs,
  onNavigate,
}: {
  snapshot: BootstrapSnapshot;
  vocabulary?: VocabularySnapshot;
  selectedTask: string | null;
  unread: number;
  nowMs: number;
  onNavigate: (route: RouteState) => void;
}) {
  const summary = snapshot.work.summary;
  return (
    <aside class="sidebar" aria-label="工作概览">
      <section class="sidebar-card summary-card">
        <div class="section-heading">
          <span>任务态势</span>
          <strong>{summary.total}</strong>
        </div>
        <div class="summary-grid">
          <span><strong>{summary.active}</strong> 活跃</span>
          <span><strong>{summary.waiting}</strong> 待验收</span>
          <span class={summary.blocked ? "is-alert" : ""}><strong>{summary.blocked}</strong> 阻塞</span>
        </div>
      </section>

      <button class="sidebar-card conversation-card" onClick={() => onNavigate({ view: "timeline" })}>
        <span class="conversation-card__mark">↗</span>
        <span><small>工作对话</small><strong>团队时间线</strong></span>
        <span class="unread-badge" aria-label={`${unread} 条未读`}>{unread}</span>
      </button>

      <section class="sidebar-section">
        <div class="section-heading"><span>成员</span><small>{snapshot.members.members.length} 人</small></div>
        <div class="member-list">
          {snapshot.members.members.map((member) => {
            const state = vocabularyItem(vocabulary?.member_state, member.state);
            return (
              <button
                class="member-card"
                type="button"
                key={member.name}
                onClick={() => onNavigate({ view: "terminal", member: member.name })}
              >
                <span class="member-avatar" style={{ background: memberColor(member.name) }}>
                  {member.name.slice(0, 1).toUpperCase()}
                </span>
                <div class="member-card__main">
                  <strong>{member.name}{member.source === "adopted" ? <em class="source-badge">临时</em> : null}</strong>
                  <small>{relativeActivity(member.silent_for, snapshot.members.snapshot_at, nowMs)}</small>
                </div>
                <span class={`member-state member-state--${member.state}`}>
                  {state.glyph ? `${state.glyph} ` : ""}{state.label}
                </span>
                {member.queued ? <span class="queue-badge">{member.queued}</span> : null}
              </button>
            );
          })}
        </div>
      </section>

      <section class="sidebar-section task-list-section">
        <div class="section-heading"><span>任务</span><small>按最近更新</small></div>
        <nav class="task-list" aria-label="任务列表">
          {snapshot.work.tasks.map((task) => (
            <button
              class={task.id === selectedTask ? "task-row is-selected" : "task-row"}
              key={task.id}
              onClick={() => onNavigate({ view: "task", taskId: task.id })}
            >
              <span class="task-row__top"><strong>{task.id}</strong><StatusPill value={task.status} vocabulary={vocabulary} /></span>
              <span class="task-row__title">{task.title}</span>
              <small>{task.assignee ?? "未指派"} · {formatTime(task.updated_at)}</small>
            </button>
          ))}
        </nav>
      </section>
    </aside>
  );
}

function DetailMeta({ detail, vocabulary }: { detail: TaskDetailSnapshot; vocabulary?: VocabularySnapshot }) {
  const task = detail.task;
  return (
    <div class="detail-meta">
      <span><small>状态</small><StatusPill value={task.status} vocabulary={vocabulary} /></span>
      <span><small>执行者</small><strong>{task.assignee ?? "未指派"}</strong></span>
      <span><small>评审者</small><strong>{task.reviewer ?? "未指定"}</strong></span>
      <span><small>更新</small><strong>{formatTime(task.updated_at)}</strong></span>
    </div>
  );
}

function TaskView({
  detail,
  loading,
  error,
  vocabulary,
  onNavigate,
}: {
  detail: TaskDetailSnapshot | null;
  loading: boolean;
  error: string | null;
  vocabulary?: VocabularySnapshot;
  onNavigate: (route: RouteState) => void;
}) {
  if (loading && !detail) return <EmptyState mark="···" title="正在读取任务" copy="从不可覆盖账本构建详情。" />;
  if (error) return <EmptyState mark="!" title="任务暂不可用" copy={error} />;
  if (!detail) return <EmptyState mark="◎" title="暂无任务" copy="任务账本中还没有可展示的任务。" />;
  const task = detail.task;
  return (
    <div class="task-view">
      <header class="content-header">
        <div>
          <p class="eyebrow">TASK / {task.id}</p>
          <h2>{task.title}</h2>
        </div>
        <span class="ledger-seal">账本只读</span>
      </header>
      <DetailMeta detail={detail} vocabulary={vocabulary} />
      <section class="panel task-brief">
        <div class="panel-heading"><h3>任务说明</h3><span>Leader · {task.leader}</span></div>
        <p>{task.description || "未填写说明"}</p>
        {task.latest ? <p class="latest-note"><span>最新进展</span>{task.latest}</p> : null}
        {detail.children.length ? (
          <div class="child-links">
            <small>子任务</small>
            {detail.children.map((child) => (
              <button key={child.id} onClick={() => onNavigate({ view: "task", taskId: child.id })}>
                {child.id} · {child.title}
              </button>
            ))}
          </div>
        ) : null}
      </section>

      <div class="detail-columns">
        <section class="panel">
          <div class="panel-heading"><h3>证据</h3><span>{task.evidence.length}</span></div>
          {task.evidence.length ? (
            <ul class="evidence-list">
              {task.evidence.map((item) => <li key={item}><span>✓</span>{item}</li>)}
            </ul>
          ) : <p class="muted">尚未提交证据。</p>}
        </section>
        <section class="panel">
          <div class="panel-heading"><h3>关联沟通</h3><span>{detail.communications.length}</span></div>
          <div class="communication-list">
            {[...detail.communications].sort((left, right) => left.at - right.at).map((item) => (
              <article key={item.timeline_seq}>
                <span class="mini-avatar" style={{ background: memberColor(item.sender) }} />
                <div><strong>{item.sender} → {item.to}</strong><p>{item.text}</p></div>
                <small>#{item.timeline_seq}</small>
              </article>
            ))}
            {!detail.communications.length ? <p class="muted">暂无关联沟通。</p> : null}
          </div>
        </section>
      </div>

      <section class="panel event-panel">
        <div class="panel-heading"><h3>不可覆盖事件流</h3><span>{detail.events.length} 条</span></div>
        <ol class="event-stream">
          {detail.events.map((event) => {
            const kind = vocabularyItem(vocabulary?.event_kind, event.kind);
            return (
              <li key={event.id}>
                <span class="event-seq">{String(event.seq).padStart(2, "0")}</span>
                <span class="event-line" />
                <div>
                  <p><strong>{kind.label}</strong><span>{event.actor}</span><time>{formatTime(event.at)}</time></p>
                  {Object.entries(event.details).map(([key, value]) => (
                    <small key={key}>{vocabularyItem(vocabulary?.event_detail_fields, key).label}: {String(value)}</small>
                  ))}
                </div>
              </li>
            );
          })}
        </ol>
      </section>
    </div>
  );
}

function mergeTimeline(current: TimelineEntry[], incoming: TimelineEntry[]): TimelineEntry[] {
  const byKey = new Map(current.map((entry) => [entry.key, entry]));
  for (const entry of incoming) byKey.set(entry.key, entry);
  return [...byKey.values()].sort(
    (left, right) => left.at - right.at || left.key.localeCompare(right.key),
  );
}

function TimelineView({
  snapshot,
  vocabulary,
  fetcher,
  onSeen,
  actor,
  onReply,
}: {
  snapshot: TimelineSnapshot;
  vocabulary?: VocabularySnapshot;
  fetcher: Fetcher;
  onSeen: (seq: number) => void;
  actor: string;
  onReply: (entry: TimelineEntry) => void;
}) {
  const [category, setCategory] = useState<TimelineCategory>("all");
  const [page, setPage] = useState(snapshot);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [loadingCategory, setLoadingCategory] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const stickToLatest = useRef(true);

  useEffect(() => {
    setPage((current) => {
      const incoming = category === "all"
        ? snapshot.entries
        : snapshot.entries.filter((entry) => entry.category === category);
      if (current.epoch !== snapshot.epoch) return { ...snapshot, entries: incoming };
      if (category === "all") {
        return { ...snapshot, entries: mergeTimeline(current.entries, incoming) };
      }
      return {
        ...current,
        epoch: snapshot.epoch,
        revision: snapshot.revision,
        entries: mergeTimeline(current.entries, incoming),
        category_counts: snapshot.category_counts,
        head_seq: snapshot.head_seq,
      };
    });
    onSeen(snapshot.head_seq);
  }, [category, snapshot, onSeen]);

  const filtered = category === "all"
    ? page.entries
    : page.entries.filter((entry) => entry.category === category);

  useEffect(() => {
    if (!stickToLatest.current) return;
    onNextFrame(() => {
      const surface = scrollRef.current;
      if (surface) surface.scrollTop = surface.scrollHeight;
    });
  }, [filtered.length]);

  async function loadOlder() {
    if (page.oldest_seq === null) return;
    const surface = scrollRef.current;
    const previousHeight = surface?.scrollHeight ?? 0;
    setLoadingOlder(true);
    try {
      const older = await fetchTimeline(category, page.oldest_seq, fetcher);
      setPage((current) => ({
        ...older,
        entries: mergeTimeline(older.entries, current.entries),
        head_seq: current.head_seq,
      }));
      onNextFrame(() => {
        if (surface) surface.scrollTop += surface.scrollHeight - previousHeight;
      });
    } finally {
      setLoadingOlder(false);
    }
  }

  async function chooseCategory(value: TimelineCategory) {
    setCategory(value);
    stickToLatest.current = true;
    setLoadingCategory(true);
    try {
      setPage(await fetchTimeline(value, undefined, fetcher));
    } catch {
      // Keep the locally filtered current page; reconnect/resync will retry.
    } finally {
      setLoadingCategory(false);
    }
  }

  function scrollLatest() {
    const surface = scrollRef.current;
    stickToLatest.current = true;
    surface?.scrollTo({ top: surface.scrollHeight, behavior: "smooth" });
  }

  return (
    <div class="timeline-view">
      <header class="content-header">
        <div><p class="eyebrow">TEAM / CONVERSATION</p><h2>工作对话时间线</h2></div>
        <button class="quiet-button" onClick={scrollLatest}>回到最新 ↓</button>
      </header>
      <div class="filter-bar" role="toolbar" aria-label="时间线分类筛选">
        {CATEGORY_ORDER.map((value) => {
          const item = value === "all"
            ? { label: "全部" }
            : vocabularyItem(vocabulary?.timeline_category, value);
          return (
            <button
              class={category === value ? "is-active" : ""}
              disabled={loadingCategory}
              onClick={() => void chooseCategory(value)}
              key={value}
            >
              {item.label}<span>{page.category_counts[value] ?? 0}</span>
            </button>
          );
        })}
      </div>
      <div
        class="timeline-scroll"
        ref={scrollRef}
        onScroll={(event) => {
          const surface = event.currentTarget;
          stickToLatest.current = surface.scrollHeight - surface.scrollTop - surface.clientHeight < 80;
        }}
      >
        {page.has_more ? <button class="load-older" disabled={loadingOlder} onClick={loadOlder}>{loadingOlder ? "读取中…" : "载入更早记录"}</button> : null}
        <div class="timeline-list">
          {filtered.map((entry, index) => {
            const group = minuteGroup(entry.ts);
            const showGroup = index === 0 || group !== minuteGroup(filtered[index - 1].ts);
            const outcome = vocabularyItem(vocabulary?.timeline_outcome, entry.outcome);
            return (
              <div key={entry.key}>
                {showGroup ? <div class="minute-divider"><span>{group}</span></div> : null}
                <article class={`timeline-entry timeline-entry--${entry.category}`}>
                  <span class="member-avatar" style={{ background: memberColor(entry.sender) }}>{entry.sender.slice(0, 1).toUpperCase()}</span>
                  <div class="timeline-entry__body">
                    <div><strong>{entry.sender}</strong><span>→ {entry.to}</span>{entry.task_id ? <em>{entry.task_id}</em> : null}</div>
                    <p>{entry.text}</p>
                    {entry.reason ? <small>{entry.reason}</small> : null}
                    {entry.attachment_ids.length ? (
                      <div class="timeline-attachments">
                        {entry.attachment_ids.map((attachmentId, attachmentIndex) => (
                          <a
                            href={`/api/v1/attachments/${encodeURIComponent(attachmentId)}`}
                            target="_blank"
                            rel="noreferrer"
                            key={attachmentId}
                          >查看图片 {attachmentIndex + 1}</a>
                        ))}
                      </div>
                    ) : null}
                  </div>
                  <div class="timeline-entry__meta">
                    <span>#{entry.seq}</span>
                    <small class={outcome.dim ? "is-dim" : ""}>{outcome.glyph} {outcome.label}</small>
                    {entry.attachment_count ? <small>附件 {entry.attachment_count}</small> : null}
                    {entry.kind === "ask" && entry.to === actor ? (
                      <button class="timeline-reply" onClick={() => onReply(entry)}>回复 ask</button>
                    ) : null}
                  </div>
                </article>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function WorkspaceView({
  snapshot,
  fetcher,
  writeToken,
}: {
  snapshot: BootstrapSnapshot;
  fetcher: Fetcher;
  writeToken: string | null;
}) {
  const [management, setManagement] = useState<MemberManagementSnapshot | null>(null);
  const [selectedPreset, setSelectedPreset] = useState("");
  const [managementError, setManagementError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const refresh = async () => {
    if (!writeToken) return;
    try {
      const next = await fetchMemberManagement(fetcher);
      setManagement(next);
      setSelectedPreset((current) => current || next.presets[0] || "");
      setManagementError(null);
    } catch (caught) {
      setManagementError(caught instanceof Error ? caught.message : "成员列表读取失败");
    }
  };

  useEffect(() => {
    void refresh();
  }, [writeToken]);

  const mutate = async (
    key: string,
    path: string,
    body: Record<string, unknown> = {},
    method = "POST",
  ) => {
    if (!writeToken) return;
    setBusy(key);
    setManagementError(null);
    try {
      await memberControl(path, writeToken, body, fetcher, method);
      await refresh();
    } catch (caught) {
      setManagementError(caught instanceof Error ? caught.message : "成员操作失败");
    } finally {
      setBusy(null);
    }
  };

  const configured = new Set(management?.members.map((member) => member.name) ?? []);
  const availablePresets = management?.presets.filter((name) => !configured.has(name)) ?? [];
  const preset = availablePresets.includes(selectedPreset)
    ? selectedPreset
    : availablePresets[0] ?? "";

  return (
    <div class="workspace-view">
      <header class="content-header"><div><p class="eyebrow">WORKSPACE</p><h2>{snapshot.workspace.slug ?? "未登记工作区"}</h2></div><span class="ledger-seal">只读视图</span></header>
      <section class="workspace-hero panel">
        <small>项目根目录</small><code>{snapshot.workspace.project_root ?? "—"}</code>
        <div class="workspace-stats">
          <span><strong>{snapshot.work.summary.total}</strong> 任务</span>
          <span><strong>{snapshot.team.members?.length ?? 0}</strong> 团队成员</span>
          <span><strong>{snapshot.timeline.head_seq}</strong> 对话记录</span>
        </div>
      </section>
      <section class="panel">
        <div class="panel-heading"><h3>{snapshot.team.name ?? snapshot.team.id ?? "未绑定团队"}</h3><span>Leader · {snapshot.team.leader ?? "—"}</span></div>
        <p>{snapshot.team.description ?? "当前工作区尚未绑定团队。"}</p>
        <div class="team-grid">
          {snapshot.team.members?.map((member) => (
            <article key={member.id}>
              <span class="member-avatar" style={{ background: memberColor(member.id) }}>{member.id.slice(0, 1).toUpperCase()}</span>
              <div><strong>{member.id}</strong><small>{member.model} · {member.responsibility}</small></div>
            </article>
          ))}
        </div>
      </section>
      {writeToken ? (
        <section class="panel member-management">
          <div class="panel-heading">
            <h3>成员管理</h3>
            <span>/member add · rm · list · /adopt</span>
          </div>
          {managementError ? <p class="management-error" role="alert">{managementError}</p> : null}
          <div class="management-add">
            <label>
              <span>从预设加入</span>
              <select value={preset} onChange={(event) => setSelectedPreset(event.currentTarget.value)}>
                {availablePresets.length ? availablePresets.map((name) => (
                  <option value={name} key={name}>{name}</option>
                )) : <option value="">没有可加入预设</option>}
              </select>
            </label>
            <button
              disabled={!preset || busy !== null}
              onClick={() => void mutate(`add:${preset}`, "/api/v1/members", { name: preset })}
            >加入成员</button>
          </div>
          <div class="managed-member-list">
            {management?.members.map((member) => (
              <article key={member.name}>
                <span class="member-avatar" style={{ background: memberColor(member.name) }}>{member.name[0].toUpperCase()}</span>
                <div>
                  <strong>{member.name}</strong>
                  <small>{member.source === "adopted" ? "临时收编 · 仅本进程有效" : "持久名册"} · {member.running ? "运行中" : "未运行"}{member.muted ? " · 已静音" : ""}</small>
                </div>
                {member.source === "roster" ? (
                  <button
                    class="danger-button"
                    disabled={busy !== null}
                    onClick={() => {
                      if (window.confirm(`确认从工作区名册移除 ${member.name}？这不会关闭其会话。`)) {
                        void mutate(`rm:${member.name}`, `/api/v1/members/${encodeURIComponent(member.name)}`, {}, "DELETE");
                      }
                    }}
                  >移除</button>
                ) : <span class="temporary-badge">TEMP</span>}
              </article>
            ))}
          </div>
          {management?.adoptable.length ? (
            <div class="adoptable-list">
              <p>发现名册外 tmux 会话；收编仅保存在当前 Web 进程，重启即失效。</p>
              {management.adoptable.map((candidate) => (
                <button
                  disabled={busy !== null}
                  key={candidate.name}
                  onClick={() => void mutate(`adopt:${candidate.name}`, "/api/v1/members/adopt", { name: candidate.name })}
                >收编 {candidate.name}<small>{candidate.commands.join(" · ")}</small></button>
              ))}
            </div>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}

function HelpView() {
  return (
    <div class="help-view">
      <header class="content-header"><div><p class="eyebrow">COMMAND PALETTE</p><h2>快捷导航</h2></div></header>
      <section class="panel help-grid">
        <div><kbd>F3</kbd><span><strong>任务视图</strong><small>回到当前任务</small></span></div>
        <div><kbd>?</kbd><span><strong>帮助</strong><small>也可使用 F1</small></span></div>
        <div><kbd>T</kbd><span><strong>切换主题</strong><small>深色 / 浅色</small></span></div>
        <div><kbd>Esc</kbd><span><strong>返回</strong><small>关闭帮助视图</small></span></div>
      </section>
      <p class="help-note">工作对话支持 @成员、Ask/Reply 与图片粘贴；任务责任动作仍以不可覆盖账本为准。退出只关闭本页显示，不会终止成员会话。</p>
    </div>
  );
}

function AttachTerminal({
  member,
  writeToken,
  fetcher,
  initialForce,
  onClose,
}: {
  member: string;
  writeToken: string;
  fetcher: Fetcher;
  initialForce: boolean;
  onClose: () => void;
}) {
  const surfaceRef = useRef<HTMLDivElement>(null);
  const connectionRef = useRef<AttachConnection | null>(null);
  const [force, setForce] = useState(initialForce);
  const [generation, setGeneration] = useState(0);
  const [status, setStatus] = useState("authorizing");
  const [holder, setHolder] = useState<LeaseHolder | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    let disposed = false;
    let cleanup = () => undefined;
    ensureXtermStylesheet();
    setStatus("authorizing");
    setHolder(null);
    setNotice(null);

    void (async () => {
      try {
        const authorization = await memberControl<{ attach_token: string }>(
          `/api/v1/members/${encodeURIComponent(member)}/attach`,
          writeToken,
          {},
          fetcher,
        );
        const { Terminal } = await import("@xterm/xterm");
        if (disposed || !surfaceRef.current) return;
        const term = new Terminal({
          disableStdin: false,
          convertEol: false,
          fontFamily: TERMINAL_FONT_FAMILY,
          fontSize: TERMINAL_FONT_SIZE,
          lineHeight: TERMINAL_LINE_HEIGHT,
          cursorBlink: true,
        });
        term.open(surfaceRef.current);
        const fit = () => {
          const surface = surfaceRef.current;
          if (!surface) return { cols: 80, rows: 24 };
          const cell = measureCell();
          const cols = Math.min(500, Math.max(20, Math.floor(surface.clientWidth / cell.width)));
          const rows = Math.min(200, Math.max(5, Math.floor(surface.clientHeight / cell.height)));
          term.resize(cols, rows);
          connectionRef.current?.resize(cols, rows);
          return { cols, rows };
        };
        const size = fit();
        const connection = connectTerminalAttach(
          member,
          { attach_token: authorization.attach_token, force, ...size },
          {
            status: setStatus,
            data: (data) => term.write(data),
            control: (message) => {
              if (message.type === "lease_denied") {
                setHolder(message.holder ?? null);
                setNotice("交互租约被占用；确认后可抢占完整接管。 ");
              } else if (message.type === "denied") {
                setNotice(message.reason ?? "完整接管被拒绝");
              }
            },
          },
        );
        connectionRef.current = connection;
        const input = term.onData((data) => connection.sendData(data));
        const observer = new ResizeObserver(fit);
        observer.observe(surfaceRef.current);
        term.focus();
        cleanup = () => {
          observer.disconnect();
          input.dispose();
          connection.disconnect();
          term.dispose();
          if (connectionRef.current === connection) connectionRef.current = null;
        };
      } catch (caught) {
        if (!disposed) {
          setStatus("closed");
          setNotice(caught instanceof Error ? caught.message : "完整接管授权失败");
        }
      }
    })();

    return () => {
      disposed = true;
      cleanup();
    };
  }, [member, writeToken, fetcher, force, generation]);

  const retryWithForce = () => {
    setForce(true);
    setGeneration((value) => value + 1);
  };
  const leave = () => {
    connectionRef.current?.exit();
    window.setTimeout(onClose, 100);
  };

  return (
    <div class="attach-overlay" role="dialog" aria-modal="true" aria-label={`${member} 完整接管`}>
      <header>
        <div><p class="eyebrow">FULL ATTACH / {member}</p><h2>完整终端接管</h2></div>
        <div class="attach-actions">
          <span class={`live-indicator live-indicator--${status}`}><i /> {status}</span>
          <button onClick={leave}>退出接管</button>
        </div>
      </header>
      <p class="attach-safety">输入直接进入成员 PTY；退出只断开 tmux client，不会关闭成员会话。</p>
      {notice ? (
        <div class="terminal-notice">
          {notice}{holder ? ` 当前持有者：${holder.owner}` : ""}
          {holder ? <button onClick={retryWithForce}>确认抢占</button> : null}
        </div>
      ) : null}
      <div class="attach-surface" ref={surfaceRef} />
    </div>
  );
}

function TerminalView({
  member,
  writeToken,
  fetcher,
}: {
  member: string;
  writeToken: string;
  fetcher: Fetcher;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<XTerm | null>(null);
  const connectionRef = useRef<TerminalConnection | null>(null);
  const frameSeqRef = useRef(0);
  const offsetRef = useRef(0);
  const leaseHeldRef = useRef(false);
  const liveActiveRef = useRef(false);
  const pendingFocusRowRef = useRef<number | null>(null);
  const cellHeightRef = useRef(TERMINAL_FONT_SIZE * TERMINAL_LINE_HEIGHT);

  const [status, setStatus] = useState<TerminalStatus>("connecting");
  const [rejection, setRejection] = useState<{ code: number; reason: string } | null>(null);
  const [leaseHeld, setLeaseHeld] = useState(false);
  const [leaseHolder, setLeaseHolder] = useState<LeaseHolder | null>(null);
  const [liveActive, setLiveActive] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const [actionBusy, setActionBusy] = useState<string | null>(null);
  const [actionNotice, setActionNotice] = useState<string | null>(null);
  const [attachOpen, setAttachOpen] = useState(false);
  const [attachInitialForce, setAttachInitialForce] = useState(false);

  useEffect(() => {
    let disposed = false;
    ensureXtermStylesheet();

    void (async () => {
      const { Terminal } = await import("@xterm/xterm");
      if (disposed || !containerRef.current) return;
      const term = new Terminal({
        disableStdin: true,
        convertEol: false,
        fontFamily: TERMINAL_FONT_FAMILY,
        fontSize: TERMINAL_FONT_SIZE,
        lineHeight: TERMINAL_LINE_HEIGHT,
      });
      term.open(containerRef.current);
      termRef.current = term;

      const connection = connectTerminalMirror(member, {
        status: (next) => {
          // BUG(T-025):非拒绝码断线后重连是全新服务端连接,没有旧连接的
          // 租约/直连态记忆(has_lease 从 false 重新算起);客户端如果不跟着
          // 清零,重连缺口里任何一次 resize/focus_input/按键都会被新连接当
          // 场按"未持租约写"关闭 4401(§6.3 白名单只有 scroll),表现为无端
          // 弹出"无法连接成员终端 unauthorized"。"connecting"覆盖首次连接,
          // "reconnecting"覆盖之后每次重连,两者都要清。
          if (next === "connecting" || next === "reconnecting") {
            leaseHeldRef.current = false;
            liveActiveRef.current = false;
            setLeaseHeld(false);
            setLiveActive(false);
          }
          setStatus(next);
        },
        rejected: (code, reason) => setRejection({ code, reason }),
        message: (message) => {
          if (message.type === "frame") {
            frameSeqRef.current = message.frame_seq;
            offsetRef.current = message.history_offset;
            setOffset(message.history_offset);
            // §5:只有租约持有者的 viewport 决定 canonical size；非持有者
            // 跟着帧里的权威 cols/rows 走，不能只信本地测量(评审 opus)。
            const term = termRef.current;
            if (
              term &&
              !leaseHeldRef.current &&
              message.cols > 0 &&
              message.rows > 0 &&
              (term.cols !== message.cols || term.rows !== message.rows)
            ) {
              term.resize(message.cols, message.rows);
            }
            termRef.current?.write(message.data);
          } else if (message.type === "idle") {
            frameSeqRef.current = message.frame_seq;
          } else if (message.type === "lease_denied") {
            leaseHeldRef.current = false;
            setLeaseHeld(false);
            setLeaseHolder(message.holder);
          } else if (message.type === "lease_acquired") {
            leaseHeldRef.current = true;
            setLeaseHeld(true);
            setLeaseHolder(null);
            const pendingRow = pendingFocusRowRef.current;
            if (pendingRow !== null) {
              pendingFocusRowRef.current = null;
              connectionRef.current?.send(focusInputMessage(frameSeqRef.current, pendingRow));
            }
          } else if (message.type === "lease_lost") {
            leaseHeldRef.current = false;
            setLeaseHeld(false);
            liveActiveRef.current = false;
            setLiveActive(false);
            setNotice("交互租约已被抢占");
          } else if (message.type === "denied") {
            liveActiveRef.current = false;
            setLiveActive(false);
            const labels: Record<string, string> = {
              "no-lease": "未持有交互租约",
              "scrolled-back": "回滚状态下不可输入",
              "stale-frame": "画面已更新，请重新点击",
              "row-not-input": "该位置当前不可输入",
            };
            setNotice(labels[message.reason] ?? message.reason);
          } else if (message.type === "live") {
            liveActiveRef.current = message.active;
            setLiveActive(message.active);
          } else if (message.type === "notice") {
            setNotice(message.text);
          }
        },
      });
      connectionRef.current = connection;
    })();

    return () => {
      disposed = true;
      connectionRef.current?.disconnect();
      connectionRef.current = null;
      termRef.current?.dispose();
      termRef.current = null;
    };
  }, [member]);

  // §5:终端窗口适配——只有租约持有者的 viewport 决定 canonical size(§5
  // 规则 1),这里始终测量/resize 本地 xterm 视图，但只有持有租约时才把
  // resize 消息发给服务端(未持有时静默跟随、不产生 tmux 副作用)。
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const applySize = () => {
      const cell = measureCell();
      cellHeightRef.current = cell.height;
      const cols = Math.max(MIN_FIT_COLS, Math.floor(container.clientWidth / cell.width));
      const rows = Math.max(MIN_FIT_ROWS, Math.floor(container.clientHeight / cell.height));
      termRef.current?.resize(cols, rows);
      if (leaseHeldRef.current) connectionRef.current?.send({ type: "resize", cols, rows });
    };
    applySize();
    const observer = new ResizeObserver(applySize);
    observer.observe(container);
    return () => observer.disconnect();
  }, [member]);

  useEffect(() => {
    if (!liveActive) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement) return;
      if (event.key === "Escape") {
        liveActiveRef.current = false;
        setLiveActive(false);
        return;
      }
      const message = directInputMessageForKeyEvent(event);
      if (!message) return;
      event.preventDefault();
      connectionRef.current?.send(message);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [liveActive]);

  const handleSurfaceClick = (event: MouseEvent) => {
    if (!containerRef.current || offsetRef.current !== 0) return;
    const rect = containerRef.current.getBoundingClientRect();
    const row = Math.max(0, Math.floor((event.clientY - rect.top) / cellHeightRef.current));
    if (!leaseHeldRef.current) {
      void acquireDirect(false, row);
      return;
    }
    connectionRef.current?.send(focusInputMessage(frameSeqRef.current, row));
  };

  const scrollBy = (delta: number) => {
    const next = Math.max(0, offsetRef.current + delta);
    offsetRef.current = next;
    setOffset(next);
    connectionRef.current?.send(scrollMessage(next));
  };

  const acquireDirect = async (force: boolean, focusRow: number | null = null) => {
    try {
      const authorization = await memberControl<{ direct_token: string }>(
        `/api/v1/members/${encodeURIComponent(member)}/direct`,
        writeToken,
        {},
        fetcher,
      );
      pendingFocusRowRef.current = focusRow;
      connectionRef.current?.send(leaseAcquireMessage(force, authorization.direct_token));
    } catch (caught) {
      setNotice(caught instanceof Error ? caught.message : "直连授权失败");
    }
  };

  const runMemberAction = async (
    action: "interrupt" | "terminate" | "restart" | "up" | "down" | "mute",
    dangerous = false,
  ) => {
    if (dangerous && !window.confirm(`确认对 ${member} 执行“${action}”？该操作会影响正在运行的任务。`)) {
      return;
    }
    setActionBusy(action);
    setActionNotice(null);
    try {
      let body: Record<string, unknown> = {};
      if (dangerous) {
        const confirmation = await memberControl<{ confirm_token: string }>(
          `/api/v1/members/${encodeURIComponent(member)}/${action}/confirm`,
          writeToken,
          {},
          fetcher,
        );
        body = { confirm_token: confirmation.confirm_token };
      }
      const result = await memberControl<{ detail?: string; muted?: boolean }>(
        `/api/v1/members/${encodeURIComponent(member)}/${action}`,
        writeToken,
        body,
        fetcher,
      );
      setActionNotice(
        action === "mute"
          ? result.muted ? "已静音：该成员消息将由 Hub 策略拒收" : "已取消静音"
          : result.detail ?? `${action} 已执行`,
      );
    } catch (caught) {
      setActionNotice(caught instanceof Error ? caught.message : "成员动作失败");
    } finally {
      setActionBusy(null);
    }
  };

  const openAttach = () => {
    setAttachInitialForce(leaseHeldRef.current);
    setAttachOpen(true);
  };

  if (rejection) {
    return (
      <EmptyState
        mark="!"
        title="无法连接成员终端"
        copy={rejection.reason || `连接被拒绝(${rejection.code})`}
      />
    );
  }

  return (
    <div class="terminal-view">
      <header class="content-header">
        <div>
          <p class="eyebrow">MEMBER / {member}</p>
          <h2>终端镜像</h2>
        </div>
        <span class={`live-indicator live-indicator--${status}`}><i /> {status}</span>
      </header>
      <section class="panel member-controls" aria-label="成员控制">
        <div>
          <strong>成员控制</strong>
          <small>所有动作由认证身份执行并写入审计</small>
        </div>
        <div class="member-control-actions">
          <button disabled={actionBusy !== null} onClick={() => void runMemberAction("interrupt")}>打断</button>
          <button disabled={actionBusy !== null} onClick={() => void runMemberAction("up")}>/up</button>
          <button class="danger-button" disabled={actionBusy !== null} onClick={() => void runMemberAction("terminate", true)}>终止</button>
          <button class="danger-button" disabled={actionBusy !== null} onClick={() => void runMemberAction("restart", true)}>/restart</button>
          <button class="danger-button" disabled={actionBusy !== null} onClick={() => void runMemberAction("down", true)}>/down</button>
          <button disabled={actionBusy !== null} onClick={() => void runMemberAction("mute")}>/mute</button>
          <button class="attach-button--primary" onClick={openAttach}>完整接管</button>
        </div>
        {actionNotice ? <p class="management-feedback">{actionNotice}</p> : null}
      </section>
      <section class="panel terminal-panel">
        <div class="terminal-toolbar">
          <span class="terminal-lease-state">
            {leaseHeld
              ? "已持有交互租约"
              : leaseHolder
                ? `当前由 ${leaseHolder.owner} 控制`
                : "只读镜像(点击画面获取控制权)"}
          </span>
          {leaseHolder && !leaseHeld ? (
            <button onClick={() => void acquireDirect(true)}>
              强制接管
            </button>
          ) : null}
          {liveActive ? <span class="live-active-badge">直连输入中 · Esc 退出</span> : null}
          <span class="terminal-toolbar__spacer" />
          <button disabled={offset === 0} onClick={() => scrollBy(-ROLLBACK_STEP)}>
            ▼ 恢复实时
          </button>
          <button onClick={() => scrollBy(ROLLBACK_STEP)}>▲ 回滚 {offset > 0 ? `(${offset})` : ""}</button>
        </div>
        {notice ? <p class="terminal-notice">{notice}</p> : null}
        <div class="terminal-surface" ref={containerRef} onClick={handleSurfaceClick} />
      </section>
      {attachOpen ? (
        <AttachTerminal
          member={member}
          writeToken={writeToken}
          fetcher={fetcher}
          initialForce={attachInitialForce}
          onClose={() => setAttachOpen(false)}
        />
      ) : null}
    </div>
  );
}

function EmptyState({ mark, title, copy }: { mark: string; title: string; copy: string }) {
  return <section class="empty-state"><p class="empty-state__mark">{mark}</p><h2>{title}</h2><p>{copy}</p></section>;
}

export function App({
  initialBootstrap,
  initialVocabulary,
  initialTaskDetail,
  initialRoute,
  pollMs = 2000,
  fetcher = fetch,
}: AppProps) {
  const [snapshot, setSnapshot] = useState<BootstrapSnapshot | null>(initialBootstrap ?? null);
  const [vocabulary, setVocabulary] = useState<VocabularySnapshot | undefined>(initialVocabulary);
  const [route, setRoute] = useState<RouteState>(initialRoute ?? routeFromPath());
  const [detail, setDetail] = useState<TaskDetailSnapshot | null>(initialTaskDetail ?? null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [nowMs, setNowMs] = useState(Date.now());
  const [exited, setExited] = useState(false);
  const [streamStatus, setStreamStatus] = useState<StreamStatus>("offline");
  const [replying, setReplying] = useState<ReplyContext | null>(null);
  const [theme, setTheme] = useState<"dark" | "light">(() => {
    const saved = localStorage.getItem(THEME_KEY);
    if (saved === "light" || saved === "dark") return saved;
    return window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark";
  });

  const selectedTask = route.view === "task"
    ? route.taskId ?? snapshot?.work.selected_default ?? null
    : detail?.task.id ?? snapshot?.work.selected_default ?? null;
  const snapshotRef = useRef(snapshot);
  const selectedTaskRef = useRef(selectedTask);
  const streamConnectedRef = useRef(false);

  const [lastSeen, setLastSeen] = useState(() => {
    const stored = readLastSeen();
    if (!initialBootstrap) return stored?.seq ?? 0;
    if (stored?.epoch === initialBootstrap.epoch) return stored.seq;
    writeLastSeen(initialBootstrap.epoch, initialBootstrap.timeline.head_seq);
    return initialBootstrap.timeline.head_seq;
  });

  const navigate = (next: RouteState, replace = false) => {
    setRoute(next);
    const method = replace ? "replaceState" : "pushState";
    window.history[method](null, "", routePath(next));
  };

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem(THEME_KEY, theme);
  }, [theme]);

  useEffect(() => {
    snapshotRef.current = snapshot;
    selectedTaskRef.current = selectedTask;
  }, [selectedTask, snapshot]);

  useEffect(() => {
    const onPopState = () => setRoute(routeFromPath());
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  useEffect(() => {
    const timer = window.setInterval(() => setNowMs(Date.now()), 10_000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target;
      const typing = target instanceof HTMLInputElement
        || target instanceof HTMLTextAreaElement
        || (target instanceof HTMLElement && target.isContentEditable);
      if (!typing && event.key.toLowerCase() === "t") {
        setTheme((current) => current === "dark" ? "light" : "dark");
      } else if (!typing && event.key === "F3") {
        event.preventDefault();
        navigate({ view: "task", taskId: selectedTask });
      } else if (!typing && (event.key === "F1" || event.key === "?")) {
        event.preventDefault();
        navigate({ view: "help" });
      } else if (event.key === "Escape" && route.view === "help") {
        navigate({ view: "task", taskId: selectedTask });
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [route.view, selectedTask]);

  useEffect(() => {
    if (exited) return;
    let cancelled = false;
    const refresh = async () => {
      try {
        const next = await fetchBootstrap(fetcher);
        if (cancelled) return;
        setSnapshot((current) => {
          const stored = readLastSeen();
          if (current?.epoch !== next.epoch && stored?.epoch !== next.epoch) {
            setLastSeen(next.timeline.head_seq);
            writeLastSeen(next.epoch, next.timeline.head_seq);
          }
          snapshotRef.current = next;
          return next;
        });
        setError(null);
      } catch (caught) {
        if (!cancelled && snapshotRef.current === null) {
          setError(caught instanceof Error ? caught.message : String(caught));
        }
      }
    };
    if (!initialBootstrap) void refresh();
    const timer = pollMs > 0 ? window.setInterval(() => {
      if (!streamConnectedRef.current) void refresh();
    }, pollMs) : undefined;
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearInterval(timer);
    };
  }, [exited, fetcher, initialBootstrap, pollMs]);

  useEffect(() => {
    if (exited || !snapshot?.session.capabilities.stream) return;

    const acceptSnapshot = (next: BootstrapSnapshot) => {
      const previous = snapshotRef.current;
      if (previous?.epoch !== next.epoch) {
        setLastSeen(next.timeline.head_seq);
        writeLastSeen(next.epoch, next.timeline.head_seq);
      }
      snapshotRef.current = next;
      setSnapshot(next);
    };
    const reload = async () => {
      const next = await fetchBootstrap(fetcher);
      acceptSnapshot(next);
      const taskId = selectedTaskRef.current;
      if (taskId) {
        try {
          const nextDetail = await fetchTaskDetail(taskId, fetcher);
          setDetail(nextDetail);
          setError(null);
        } catch (caught) {
          setError(caught instanceof Error ? caught.message : String(caught));
        }
      }
      return { epoch: next.epoch, revisions: next.revisions };
    };
    const disconnect = connectEventStream({
      current: () => {
        const current = snapshotRef.current;
        return current
          ? { epoch: current.epoch, revisions: current.revisions }
          : { epoch: "", revisions: {} };
      },
      resync: reload,
      delta: async (frame) => {
        if (frame.domain === "work") return reload();
        if (frame.domain === "timeline") {
          setSnapshot((current) => {
            if (!current) return current;
            const next = {
              ...current,
              revisions: { ...current.revisions, timeline: frame.revision ?? current.revisions.timeline },
              timeline: applyTimelineDelta(current.timeline, frame),
            };
            snapshotRef.current = next;
            return next;
          });
          return;
        }
        if (frame.domain === "health") {
          setSnapshot((current) => {
            if (!current) return current;
            const faults = new Map(current.health.faults.map((fault) => [fault.key, fault]));
            for (const operation of frame.ops ?? []) {
              const fault = operation.fault as Fault | undefined;
              if (!fault) continue;
              if (operation.op === "raise") faults.set(fault.key, fault);
              if (operation.op === "clear") faults.delete(fault.key);
            }
            const next = {
              ...current,
              revisions: { ...current.revisions, health: frame.revision ?? current.revisions.health },
              health: {
                ...current.health,
                revision: frame.revision ?? current.health.revision,
                degraded: faults.size > 0,
                faults: [...faults.values()],
              },
            };
            snapshotRef.current = next;
            return next;
          });
        }
      },
      status: (status) => {
        streamConnectedRef.current = status === "connected";
        setStreamStatus(status);
      },
    });
    return disconnect;
  }, [exited, fetcher, snapshot?.session.capabilities.stream]);

  useEffect(() => {
    if (initialVocabulary) return;
    let cancelled = false;
    fetchVocabulary(fetcher).then((next) => {
      if (!cancelled) setVocabulary(next);
    }).catch(() => undefined);
    return () => { cancelled = true; };
  }, [fetcher, initialVocabulary]);

  useEffect(() => {
    if (exited) return;
    if (!selectedTask) {
      setDetail(null);
      return;
    }
    if (detail?.task.id === selectedTask && (initialTaskDetail || pollMs === 0)) return;
    let cancelled = false;
    const refresh = async () => {
      setLoadingDetail(true);
      try {
        const next = await fetchTaskDetail(selectedTask, fetcher);
        if (!cancelled) {
          setDetail(next);
          setError(null);
        }
      } catch (caught) {
        if (!cancelled) setError(caught instanceof Error ? caught.message : String(caught));
      } finally {
        if (!cancelled) setLoadingDetail(false);
      }
    };
    void refresh();
    const timer = pollMs > 0 ? window.setInterval(() => {
      if (!streamConnectedRef.current) void refresh();
    }, pollMs) : undefined;
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearInterval(timer);
    };
  }, [detail?.task.id, exited, fetcher, initialTaskDetail, pollMs, selectedTask]);

  const markSeen = useMemo(() => (seq: number) => {
    if (!snapshot) return;
    setLastSeen(seq);
    writeLastSeen(snapshot.epoch, seq);
  }, [snapshot]);

  if (exited) {
    return (
      <main class="exit-screen">
        <span class="exit-screen__mark">AMUX</span>
        <h1>观察会话已退出</h1>
        <p>成员与任务继续运行。本页没有终止任何后台会话。</p>
        <button onClick={() => setExited(false)}>返回控制台</button>
      </main>
    );
  }

  if (!snapshot) {
    return <main class="app-shell"><EmptyState mark={error ? "!" : "◎"} title={error ? "控制台暂不可用" : "正在连接工作区"} copy={error ?? "正在读取一致 snapshot…"} /></main>;
  }

  const unread = Math.max(0, snapshot.timeline.head_seq - lastSeen);
  const workspaceTitle = snapshot.workspace.slug ?? "未登记工作区";

  return (
    <main class="app-shell">
      <header class="app-header">
        <button class="brand" onClick={() => navigate({ view: "task", taskId: selectedTask })}>
          <span class="brand__mark">A</span>
          <span><strong>amux</strong><small>{workspaceTitle} · {snapshot.workspace.project_root ?? "路径不可用"}</small></span>
        </button>
        <nav class="top-nav" aria-label="主导航">
          <button class={route.view === "task" ? "is-active" : ""} onClick={() => navigate({ view: "task", taskId: selectedTask })}>任务</button>
          <button class={route.view === "timeline" ? "is-active" : ""} onClick={() => navigate({ view: "timeline" })}>对话</button>
          <button class={route.view === "workspace" ? "is-active" : ""} onClick={() => navigate({ view: "workspace" })}>工作区</button>
        </nav>
        <div class="header-actions">
          <span class={`live-indicator live-indicator--${streamStatus}`}><i /> {snapshot.session.capabilities.stream ? streamStatus : "snapshot"} · r{snapshot.revisions.work ?? 0}</span>
          <button aria-label="切换深浅主题" title="切换主题 (T)" onClick={() => setTheme((current) => current === "dark" ? "light" : "dark")}>{theme === "dark" ? "☼" : "◐"}</button>
          <button aria-label="打开帮助" title="帮助 (?)" onClick={() => navigate({ view: "help" })}>?</button>
          <button class="exit-button" onClick={() => setExited(true)}>退出</button>
        </div>
      </header>
      <HealthBanner snapshot={snapshot} />
      <div class="workspace-shell">
        <Sidebar snapshot={snapshot} vocabulary={vocabulary} selectedTask={selectedTask} unread={unread} nowMs={nowMs} onNavigate={navigate} />
        <section class="content-surface">
          {route.view === "task" ? <TaskView detail={detail} loading={loadingDetail} error={error} vocabulary={vocabulary} onNavigate={navigate} /> : null}
          {route.view === "timeline" ? <TimelineView snapshot={snapshot.timeline} vocabulary={vocabulary} fetcher={fetcher} onSeen={markSeen} actor={snapshot.session.actor} onReply={(entry) => setReplying(replyContext(entry))} /> : null}
          {route.view === "workspace" ? (
            <WorkspaceView
              snapshot={snapshot}
              fetcher={fetcher}
              writeToken={snapshot.session.write_token}
            />
          ) : null}
          {route.view === "help" ? <HelpView /> : null}
          {route.view === "terminal" && snapshot.session.write_token ? (
            <TerminalView
              member={route.member}
              writeToken={snapshot.session.write_token}
              fetcher={fetcher}
            />
          ) : null}
        </section>
      </div>
      {snapshot.session.capabilities.compose && (route.view === "task" || route.view === "timeline") ? (
        <ComposeBar
          snapshot={snapshot}
          taskId={route.view === "task" ? selectedTask : null}
          preferLeader={route.view === "task"}
          reply={replying}
          onReplyChange={setReplying}
          fetcher={fetcher}
        />
      ) : null}
    </main>
  );
}
