import assert from "node:assert/strict";
import test from "node:test";
import {
  artifactHref,
  checkHeadline,
  errorDetail,
  hasRunningJob,
  reportMatches,
  statusLabel,
} from "./model.ts";

test("published artifact links stay on the authenticated API path", () => {
  assert.equal(
    artifactHref("/artifacts/data/reports/backtest/example/run/report.html"),
    "/api/management/artifacts/data/reports/backtest/example/run/report.html",
  );
  assert.equal(artifactHref("/artifacts/../secret.txt"), null);
  assert.equal(artifactHref("https://example.test/report.html"), null);
  assert.equal(artifactHref(null), null);
});

test("job status and running slot use the persisted vocabulary", () => {
  assert.equal(statusLabel("interrupted"), "已中断");
  assert.equal(statusLabel("custom"), "custom");
  assert.equal(hasRunningJob([{ status: "failed" }, { status: "running" }]), true);
  assert.equal(hasRunningJob([{ status: "succeeded" }]), false);
});

test("report search matches strategy, run and name without treating blanks as filters", () => {
  const report = { name: "trend", strategy: "btc_trend_following_long", run_id: "run-1", url: "/artifacts/x" };
  assert.equal(reportMatches(report, "  BTC "), true);
  assert.equal(reportMatches(report, "run-1"), true);
  assert.equal(reportMatches(report, "missing"), false);
  assert.equal(reportMatches(report, "   "), true);
});

test("check headlines keep stale results visible and unqualified", () => {
  const missing = checkHeadline({ data: null, error: "状态文件尚未生成或暂时无法读取", stale: true });
  assert.equal(missing.title, "尚无检查结果");
  const stale = checkHeadline({
    data: { passed: true, full_tests: false, checked_at: "2026-08-01T00:00:00+00:00" },
    error: null,
    stale: true,
  });
  assert.match(stale.title, /快速检查通过/);
  assert.match(stale.detail, /不能视为当前正常/);
  assert.equal(stale.tone, "warn");
});

test("API error bodies surface the server detail", () => {
  assert.equal(errorDetail({ detail: "已有任务运行，请等待完成或取消后再试" }, "失败"), "已有任务运行，请等待完成或取消后再试");
  assert.equal(errorDetail({ detail: [{ msg: "x" }] }, "失败"), "失败");
  assert.equal(errorDetail(null, "失败"), "失败");
});
