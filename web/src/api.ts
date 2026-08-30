import type {
  BootstrapSnapshot,
  TaskDetailSnapshot,
  TimelineCategory,
  TimelineSnapshot,
  VocabularySnapshot,
} from "./model";

export type Fetcher = typeof fetch;

export class ApiFailure extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
  ) {
    super(message);
  }
}

async function getJSON<T>(fetcher: Fetcher, path: string): Promise<T> {
  const response = await fetcher(path, {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    let message = `请求失败 (${response.status})`;
    let code: string | undefined;
    try {
      const payload = (await response.json()) as {
        error?: { message?: string; code?: string };
      };
      message = payload.error?.message ?? message;
      code = payload.error?.code;
    } catch {
      // Keep the status-based fallback when a proxy returned non-JSON.
    }
    throw new ApiFailure(message, response.status, code);
  }
  return (await response.json()) as T;
}

export function fetchBootstrap(fetcher: Fetcher = fetch): Promise<BootstrapSnapshot> {
  return getJSON(fetcher, "/api/v1/bootstrap");
}

export function fetchVocabulary(fetcher: Fetcher = fetch): Promise<VocabularySnapshot> {
  return getJSON(fetcher, "/api/v1/vocabulary");
}

export function fetchTaskDetail(
  taskId: string,
  fetcher: Fetcher = fetch,
): Promise<TaskDetailSnapshot> {
  return getJSON(fetcher, `/api/v1/work/tasks/${encodeURIComponent(taskId)}`);
}

export function fetchTimeline(
  category: TimelineCategory,
  beforeSeq?: number,
  fetcher: Fetcher = fetch,
): Promise<TimelineSnapshot> {
  const query = new URLSearchParams({ category, limit: "200" });
  if (beforeSeq !== undefined) query.set("before_seq", String(beforeSeq));
  return getJSON(fetcher, `/api/v1/timeline?${query.toString()}`);
}
