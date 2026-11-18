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
