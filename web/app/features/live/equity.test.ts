import assert from "node:assert/strict";
import test from "node:test";
import { requestEquityRebuild } from "./equity.ts";

test("asset replay submits one account and polls the same persisted job", async () => {
  const original = globalThis.fetch;
  const requests: { url: string; options?: RequestInit }[] = [];
  globalThis.fetch = async (url, options) => {
    requests.push({ url: String(url), options });
    return new Response(JSON.stringify({ id: "job-1", status: "queued" }), { status: options?.method === "POST" ? 202 : 200 });
  };
  try {
    const signal = new AbortController().signal;
    const job = await requestEquityRebuild(7, signal);
    await requestEquityRebuild(7, signal, job.id);
    assert.equal(requests[0].url, "/api/exchange-accounts/7/equity-rebuilds");
    assert.equal(requests[0].options?.method, "POST");
    assert.equal(requests[0].options?.body, "{}");
    assert.equal(requests[1].url, "/api/exchange-accounts/7/equity-rebuilds/job-1");
    assert.equal(requests[1].options?.method, "GET");
    assert.equal(requests[1].options?.credentials, "same-origin");
    assert.equal(requests[1].options?.signal, signal);
  } finally { globalThis.fetch = original; }
});

test("asset replay surfaces permission and duplicate-job failures", async () => {
  const original = globalThis.fetch;
  try {
    for (const status of [403, 409, 503]) {
      globalThis.fetch = async () => new Response(JSON.stringify({ detail: "fixture failure" }), { status });
      await assert.rejects(requestEquityRebuild(7, new AbortController().signal), /fixture failure/);
    }
  } finally { globalThis.fetch = original; }
});
