import assert from "node:assert/strict";
import test from "node:test";
import nextConfig from "../../../next.config.ts";

test("strategy package routes reach the Platform API through the same-origin proxy", async () => {
  const rewrites = await nextConfig.rewrites?.();
  assert.ok(Array.isArray(rewrites));
  const apiOrigin = process.env.SEEALPHA_API_URL ?? "http://127.0.0.1:8000";

  for (const path of ["/api/strategy-packages", "/api/strategy-packages/:path+"]) {
    assert.deepEqual(rewrites.find(({ source }) => source === path), {
      source: path,
      destination: `${apiOrigin}${path}`,
    });
  }
});
