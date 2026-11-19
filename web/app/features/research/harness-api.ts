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
      throw new Error("当前账户没有使用 AI Harness 或对应开发资源的权限。");
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

export type ResearchSelection = {
  research_tenant_id: number;
  research_account_id: string;
  research_dataset_ref: string;
};

export function harnessStart(prompt: string, research?: ResearchSelection) {
  return request<HarnessTask>(base + "/tasks", {
    method: "POST",
    body: JSON.stringify({prompt, queue_if_busy: !research, ...research}),
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

export type DevelopmentEngineSelection = {
  tenant_id: number;
  account_id: string;
  dataset_ref: string;
};

export type DevelopmentDataWindow = DevelopmentEngineSelection & {
  start: string;
  end: string;
};

export type DevelopmentDataJob = {
  id: string;
  action: string;
  status: string;
  progress?: string;
  error?: string | null;
  created_at?: string;
  result?: Record<string, unknown> | null;
};

export type DevelopmentDataPlan = {
  missing_days?: number;
  archive_days?: number;
  message?: string;
};

const developmentEngine = base + "/development-engine";

export function engineDataPlan(payload: DevelopmentDataWindow) {
  return request<DevelopmentDataPlan>(developmentEngine + "/data/plan", {
    method: "POST", body: JSON.stringify(payload),
  });
}

export function enginePrepareData(payload: DevelopmentDataWindow) {
  return request<DevelopmentDataJob>(developmentEngine + "/data/prepare", {
    method: "POST", body: JSON.stringify(payload),
  });
}

export async function engineDataJobs(selection: DevelopmentEngineSelection) {
  const query = new URLSearchParams({
    tenant_id: String(selection.tenant_id),
    account_id: selection.account_id,
    dataset_ref: selection.dataset_ref,
  });
  const result = await request<{jobs:DevelopmentDataJob[]}>(
    developmentEngine + "/data/jobs?" + query,
  );
  return result.jobs;
}

export function engineCancelDataJob(selection: DevelopmentEngineSelection, id: string) {
  return request<DevelopmentDataJob>(
    developmentEngine + "/data/jobs/" + encodeURIComponent(id) + "/cancel",
    {method: "POST", body: JSON.stringify(selection)},
  );
}

export function engineResetDevelopment(tenantId: number, confirmation: string) {
  return request<{status:string,tenant_id:number,backup:string,historical_data_preserved:boolean}>(
    developmentEngine + "/reset",
    {method: "POST",body: JSON.stringify({tenant_id:tenantId,confirmation})},
  );
}
