import assert from "node:assert/strict";
import test from "node:test";
import {
  canAccessAdminConsole,
  copyTenantBody,
  developmentCreateBody,
  errorDetail,
  productionTenant,
  purposeLabel,
  sessionFromAuth,
  sessionHomePath,
  shareTenantBody,
  sortTenants,
  statusLabel,
  tenantActionPath,
  tenantsPath,
  usersPath,
} from "./model.ts";

test("auth payload exposes only the actual signed-in identity", () => {
  assert.equal(sessionFromAuth(null), null);
  assert.equal(sessionFromAuth({}), null);
  const admin = sessionFromAuth({ user: { id: 1, username: "admin" }, is_admin: true });
  assert.deepEqual(admin, { user: { id: 1, username: "admin" }, isAdmin: true });
  const user = sessionFromAuth({ user: { id: 2, username: "alice" } });
  assert.deepEqual(user, { user: { id: 2, username: "alice" }, isAdmin: false });
});

test("production tenant is the default and sorts ahead of development tenants", () => {
  const tenants = [
    { id: 3, purpose: "development", name: "乙" },
    { id: 2, purpose: "development", name: "甲" },
    { id: 1, purpose: "production", name: "默认正式" },
  ];
  assert.equal(productionTenant(tenants)?.name, "默认正式");
  assert.equal(productionTenant([]), null);
  assert.deepEqual(sortTenants(tenants).map((tenant) => tenant.id), [1, 2, 3]);
});

test("tenant requests stay on development copies and do not send a second production", () => {
  assert.deepEqual(developmentCreateBody("  研究  ", "  说明  "), {
    name: "研究",
    description: "说明",
    purpose: "development",
  });
  assert.deepEqual(developmentCreateBody("研究", "   "), { name: "研究", purpose: "development" });
  assert.equal(developmentCreateBody("正式", "").purpose, "development");
  assert.deepEqual(copyTenantBody("  副本  "), { name: "副本" });
  assert.deepEqual(shareTenantBody(" 起点 ", " bob "), { name: "起点", username: "bob" });
});

test("identity paths and tenant labels", () => {
  assert.equal(usersPath(), "/api/users");
  assert.equal(tenantsPath(), "/api/tenants");
  assert.equal(tenantActionPath(4, "copy"), "/api/tenants/4/copy");
  assert.equal(tenantActionPath(4, "share"), "/api/tenants/4/share");
  assert.equal(purposeLabel("production"), "正式");
  assert.equal(purposeLabel("development"), "开发");
  assert.equal(statusLabel("archived"), "已归档");
});

test("API error bodies surface the server detail", () => {
  assert.equal(errorDetail({ detail: "仅管理员可创建额外的正式租户。" }, "失败"), "仅管理员可创建额外的正式租户。");
  assert.equal(errorDetail({ detail: [{ msg: "x" }] }, "失败"), "失败");
  assert.equal(errorDetail(null, "失败"), "失败");
});

test("admin and ordinary users have separate destinations", () => {
  const ordinary = sessionFromAuth({ user: { id: 2, username: "alice" } });
  const administrator = sessionFromAuth({ user: { id: 1, username: "admin" }, is_admin: true });
  assert.ok(ordinary && administrator);
  assert.equal(sessionHomePath(ordinary), "/app");
  assert.equal(sessionHomePath(administrator), "/admin");
  assert.equal(canAccessAdminConsole(ordinary), false);
  assert.equal(canAccessAdminConsole(administrator), true);
});
