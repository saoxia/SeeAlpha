import { errorDetail } from "./model";

export type ResearchStrategy = {
  id: string;
  label: string;
  symbol: string;
  description: string;
};

export type ResearchAction = {
  id: string;
  label: string;
  description: string;
};

export type ResearchJob = {
  id: string;
  action: string;
  label?: string;
  status: string;
  created_at?: string;
  finished_at?: string | null;
  returncode?: number | null;
  parameters?: Record<string, string>;
  error?: string | null;
  report_urls?: string[];
  log?: string;
  log_truncated?: boolean;
};

export type ResearchReport = {
  name: string;
  strategy: string;
  run_id: string;
  modified_at: string;
  url: string;
  summary_url: string | null;
};

export type ResearchState = {
  server_time: string;
  checks: {
    data: {
      passed?: boolean;
      checked_at?: string;
      full_tests?: boolean;
      steps?: Array<{ name: string; passed: boolean; summary?: string }>;
    } | null;
    error: string | null;
    age_seconds: number | null;
    stale: boolean;
  };
  strategies: ResearchStrategy[];
  actions: ResearchAction[];
  jobs: ResearchJob[];
  reports: ResearchReport[];
  report_count: number;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.body && !headers.has("content-type")) headers.set("content-type", "application/json");
  const response = await fetch(path, {
    cache: "no-store",
    credentials: "same-origin",
    ...init,
    headers,
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(errorDetail(payload, "请求失败，请稍后重试。"));
  }
  return response.json() as Promise<T>;
}

export function fetchResearchState(signal?: AbortSignal) {
  return request<ResearchState>("/api/management/state", { signal });
}

export function fetchResearchJob(identity: string, signal?: AbortSignal) {
  return request<ResearchJob>(`/api/management/jobs/${encodeURIComponent(identity)}`, { signal });
}

export async function startResearchJob(action: string, parameters: Record<string, string> = {}) {
  const result = await request<{ job: ResearchJob }>("/api/management/jobs", {
    method: "POST",
    body: JSON.stringify({ action, parameters }),
  });
  return result.job;
}

export async function cancelResearchJob(identity: string) {
  const result = await request<{ job: ResearchJob }>(
    `/api/management/jobs/${encodeURIComponent(identity)}/cancel`,
    { method: "POST", body: "{}" },
  );
  return result.job;
}
