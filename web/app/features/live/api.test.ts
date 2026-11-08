import assert from "node:assert/strict";
import test from "node:test";

import { fetchPortfolio } from "./api.ts";

test("manual asset refresh bypasses caches for selected account and overview", async () => {
  const original = globalThis.fetch;
  const requests: string[] = [];
  globalThis.fetch = async (url, options) => {
    requests.push(String(url));
    assert.equal(options?.cache, "no-store");
    assert.equal(options?.credentials, "same-origin");
    return new Response(JSON.stringify({ assets: [] }), { status: 200 });
  };
  try {
    const signal = new AbortController().signal;
    await fetchPortfolio(7, signal);
    await fetchPortfolio(7, signal, true);
    await fetchPortfolio(null, signal, true);
    assert.deepEqual(requests, [
      "/api/exchange-accounts/7/portfolio",
      "/api/exchange-accounts/7/portfolio?refresh=true",
      "/api/exchange-accounts/portfolio?refresh=true",
    ]);
  } finally {
    globalThis.fetch = original;
  }
});
