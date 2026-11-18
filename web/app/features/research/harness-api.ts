import { errorDetail } from "./model";

export type HarnessStatus = {
  enabled: boolean;
  available: boolean;
  runtime_configured: boolean;
  runtime_layout?: string;
};

export type HarnessTask = {
  id: string;
  status: "queued" | "starting" | "running" | "cancelling" |
    "completed" | "failed" | "cancelled" | "interrupted";
  turn: number;
  created_at: string;
  updated_at: string;
  final_response: string | null;
  error: string | null;
};

export type HarnessHistoryTask = Pick<HarnessTask,
  "id" | "status" | "turn" | "created_at" | "updated_at" | "error">;

export type HarnessEvent = {
  sequence: number;
  timestamp: string;
  type: "progress" | "terminal" | "artifact_ready";
  stage: string | null;
  notifications: number | null;
  status: string | null;
};

export type HarnessArtifact = {
  artifact_id: string;
  filename: string;
  kind: string;
  size: number;
  sha256: string;
  review_status?: string;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.body && !headers.has("content-type")) headers.set("content-type", "application/json");
  const result = await fetch(path, {
    credentials: "same-origin",
    cache: "no-store",
    ...init,
    headers,
  });
  if (!result.ok) {
    if (result.status === 403) {
      throw new Error("当前 Harness 执行环境还不支持普通用户安全隔离，研究任务暂未开放。");
    }
    const payload: unknown = await result.json().catch(() => null);
    throw new Error(errorDetail(payload, "无法访问 Harness，请检查服务连接。"));
  }
  return result.json() as Promise<T>;
}

const base = "/api/harness";

export function harnessStatus(signal?: AbortSignal) {
  return request<HarnessStatus>(base + "/status", {signal});
}

export function harnessStart(prompt: string) {
  return request<HarnessTask>(base + "/tasks", {
    method: "POST",
    body: JSON.stringify({prompt, queue_if_busy: true}),
  });
}

export async function harnessHistory(signal?: AbortSignal) {
  const result = await request<{tasks:HarnessHistoryTask[]}>(base + "/tasks",{signal});
  return result.tasks;
}

export function harnessTimeline(id: string, after=0, signal?: AbortSignal) {
  return request<{events:HarnessEvent[],next_cursor:number,has_more:boolean}>(
    base + "/tasks/" + encodeURIComponent(id) +
    "/timeline?after=" + after, {signal});
}

export function harnessImport(id:string, sourceFile:string) {
  return request<HarnessArtifact>(
    base + "/tasks/" + encodeURIComponent(id) + "/artifacts/import",
    {method:"POST",body:JSON.stringify({source_file:sourceFile})},
  );
}

export function harnessGet(id: string, signal?: AbortSignal) {
  return request<HarnessTask>(base + "/tasks/" + encodeURIComponent(id), {signal});
}

export function harnessContinue(id: string, prompt: string) {
  return request<HarnessTask>(base + "/tasks/" + encodeURIComponent(id) + "/continue", {
    method: "POST",
    body: JSON.stringify({prompt, queue_if_busy: true}),
  });
}

export function harnessCancel(id: string) {
  return request<HarnessTask>(base + "/tasks/" + encodeURIComponent(id) + "/cancel", {
    method: "POST",
  });
}

export async function harnessArtifacts(id: string, signal?: AbortSignal) {
  const payload = await request<{artifacts: HarnessArtifact[]}>(
    base + "/tasks/" + encodeURIComponent(id) + "/artifacts", {signal},
  );
  return payload.artifacts;
}

export const HARNESS_FINISHED = new Set(["completed","failed","cancelled","interrupted"]);
export const harnessTaskStorageKey = "seealpha.research.harness.latest-task-id";
