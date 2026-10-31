export type CheckSnapshot = {
  data: {
    passed?: boolean;
    checked_at?: string;
    full_tests?: boolean;
    steps?: Array<{ name: string; passed: boolean; summary?: string }>;
  } | null;
  error: string | null;
  stale: boolean;
  age_seconds?: number | null;
};

export type ResearchReport = {
  name: string;
  strategy: string;
  run_id: string;
  url: string;
};

const STATUS_LABELS: Record<string, string> = {
  running: "运行中",
  succeeded: "已完成",
  failed: "失败",
  cancelled: "已取消",
  interrupted: "已中断",
  skipped: "已跳过",
};

const ARTIFACT_PREFIX = "/artifacts/";

export function statusLabel(status: string) {
  return STATUS_LABELS[status] ?? status;
}

export function hasRunningJob(jobs: Array<{ status: string }>) {
  return jobs.some((job) => job.status === "running");
}

export function artifactHref(url: string | null | undefined) {
  if (!url?.startsWith(ARTIFACT_PREFIX) || url.includes("\\") || url.includes("..")) return null;
  return `/api/management${url}`;
}

export function errorDetail(payload: unknown, fallback: string) {
  if (!payload || typeof payload !== "object" || !("detail" in payload)) return fallback;
  const detail = payload.detail;
  return typeof detail === "string" && detail ? detail : fallback;
}

export function reportMatches(report: ResearchReport, query: string) {
  const needle = query.trim().toLocaleLowerCase();
  if (!needle) return true;
  return [report.strategy, report.run_id, report.name].some((part) =>
    part.toLocaleLowerCase().includes(needle),
  );
}

export function checkHeadline(checks: CheckSnapshot) {
  if (!checks.data || checks.error) {
    return {
      title: "尚无检查结果",
      detail: checks.error || "状态文件尚未生成或暂时无法读取",
      tone: "unknown" as const,
    };
  }
  const scope = checks.data.full_tests ? "完整检查" : "快速检查";
  const result = checks.data.passed ? "通过" : "未通过";
  const freshness = checks.stale ? "结果已超过一天，不能视为当前正常。" : "结果仍在有效期内。";
  const when = checks.data.checked_at ? `${checks.data.checked_at} ` : "";
  return {
    title: `${scope}${result}`,
    detail: `${when}${freshness}`,
    tone: checks.data.passed && !checks.stale ? ("ok" as const) : ("warn" as const),
  };
}
