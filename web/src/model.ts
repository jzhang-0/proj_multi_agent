export type RevisionMap = Record<string, number>;

export interface SessionSnapshot {
  actor: string;
  write_token: string | null;
  epoch: string;
  epoch_started_at: number;
  server_time_at: number;
  revisions: RevisionMap;
  capabilities: Record<string, boolean>;
}

export interface WorkspaceSnapshot {
  epoch: string;
  revision: number;
  registered: boolean;
  slug: string | null;
  project_root: string | null;
}

export interface TeamMember {
  id: string;
  role: string;
  model: string;
  effort: string;
  speed: string;
  responsibility: string;
}

export interface TeamSnapshot {
  epoch: string;
  revision: number;
  bound: boolean;
  id?: string;
  name?: string;
  description?: string;
  leader?: string;
  members?: TeamMember[];
}

export interface TaskSummary {
  leader: string;
  active: number;
  waiting: number;
  blocked: number;
  total: number;
  by_status: Record<string, number>;
}

export interface TaskListItem {
  id: string;
  title: string;
  status: string;
  assignee: string | null;
  reviewer: string | null;
  parent_id: string | null;
  completed: boolean;
  created_at: number;
  updated_at: number;
  created_ts: string;
  updated_ts: string;
}

export interface WorkSnapshot {
  epoch: string;
  revision: number;
  summary: TaskSummary;
  tasks: TaskListItem[];
  selected_default: string | null;
}

export interface MemberSnapshot {
  name: string;
  state: string;
  queued: number;
  silent_for: number | null;
  alive: boolean;
  source: string;
}

export interface MembersSnapshot {
  epoch: string;
  revision: number;
  snapshot_at: number;
  members: MemberSnapshot[];
}

export interface ManagedMember {
  name: string;
  source: "roster" | "adopted";
  temporary: boolean;
  muted: boolean;
  running: boolean;
}

export interface AdoptableMember {
  name: string;
  commands: string[];
}

export interface MemberManagementSnapshot {
  members: ManagedMember[];
  adoptable: AdoptableMember[];
  presets: string[];
}

export interface Fault {
  key: string;
  kind: string;
  target: string;
  detail: string;
}

export interface HealthSnapshot {
  epoch: string;
  revision: number;
  degraded: boolean;
  faults: Fault[];
}

export type TimelineCategory = "all" | "human" | "ai" | "task" | "control";

export interface TimelineEntry {
  seq: number;
  key: string;
  at: number;
  ts: string;
  sender: string;
  to: string;
  text: string;
  outcome: string;
  reason: string;
  task_id: string | null;
  attachment_count: number;
  category: Exclude<TimelineCategory, "all">;
  has_body: boolean;
  kind: "ask" | "reply" | null;
  reply_to: string | null;
  attachment_ids: string[];
}

export interface AttachmentUpload {
  id: string;
  name: string;
  media_type: string;
  width: number;
  height: number;
  size: number;
  download_url: string;
}

export interface MessageRequest {
  to?: string;
  text?: string;
  kind?: "message" | "ask" | "reply";
  task_id?: string;
  reply_to?: string;
  attachment_ids?: string[];
}

export interface MessageReceipt {
  id: string;
  to: string;
  kind: "message" | "ask" | "reply";
  reply_to: string | null;
  task_id: string | null;
  attachment_ids: string[];
}

export interface TimelineSnapshot {
  epoch: string;
  revision: number;
  entries: TimelineEntry[];
  category_counts: Record<TimelineCategory, number>;
  head_seq: number;
  oldest_seq: number | null;
  has_more: boolean;
}

export interface BootstrapSnapshot {
  epoch: string;
  revisions: RevisionMap;
  session: SessionSnapshot;
  workspace: WorkspaceSnapshot;
  team: TeamSnapshot;
  work: WorkSnapshot;
  members: MembersSnapshot;
  health: HealthSnapshot;
  timeline: TimelineSnapshot;
}

export interface VocabularyItem {
  value?: string;
  key?: string;
  label: string;
  glyph?: string;
  dim?: boolean;
}

export interface VocabularySnapshot {
  epoch: string;
  task_status: VocabularyItem[];
  event_kind: VocabularyItem[];
  member_state: VocabularyItem[];
  timeline_category: VocabularyItem[];
  timeline_outcome: VocabularyItem[];
  event_detail_fields: VocabularyItem[];
}

export interface TaskDetailTask {
  id: string;
  title: string;
  description: string;
  leader: string;
  parent_id: string | null;
  status: string;
  assignee: string | null;
  reviewer: string | null;
  accepted_by: string | null;
  completed: boolean;
  latest: string;
  created_at: number;
  updated_at: number;
  created_ts: string;
  updated_ts: string;
  evidence: string[];
}

export interface TaskChild {
  id: string;
  title: string;
  status: string;
}

export interface TaskEvent {
  seq: number;
  id: string;
  kind: string;
  actor: string;
  at: number;
  ts: string;
  details: Record<string, unknown>;
}

export interface TaskCommunication {
  timeline_seq: number;
  sender: string;
  to: string;
  text: string;
  attachment_count: number;
  at: number;
  ts: string;
}

export interface TaskDetailSnapshot {
  epoch: string;
  revision: number;
  task: TaskDetailTask;
  children: TaskChild[];
  events: TaskEvent[];
  communications: TaskCommunication[];
}

export type RouteState =
  | { view: "task"; taskId: string | null }
  | { view: "timeline" }
  | { view: "workspace" }
  | { view: "help" }
  | { view: "terminal"; member: string };
