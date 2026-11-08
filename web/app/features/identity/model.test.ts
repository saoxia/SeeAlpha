import assert from "node:assert/strict";
import test from "node:test";
import {
  bannerText,
  copyTenantBody,
  developmentCreateBody,
  errorDetail,
  impersonatePath,
  productionTenant,
  purposeLabel,
  sessionFromAuth,
  shareTenantBody,
  sortTenants,
  statusLabel,
  stopImpersonationPath,
  tenantActionPath,
  tenantsPath,
  usersPath,
} from "./model.ts";

test("auth payload keeps the acted-as user separate from the admin actor", () => {
  assert.equal(sessionFromAuth(null), null);
  assert.equal(sessionFromAuth({}), null);
  const session = sessionFromAuth({
    user: { id: 2, username: "alice" },
    actor: { id: 1, username: "admin" },
    impersonating: true,
    is_admin: true,
  });
  assert.deepEqual(session, {
    user: { id: 2, username: "alice" },
    actor: { id: 1, username: "admin" },
    impersonating: true,
    isAdmin: true,
  });
  const plain = sessionFromAuth({ user: { id: 1, username: "admin" }, is_admin: true });
  assert.equal(plain?.impersonating, false);
  assert.equal(plain?.actor, null);
  assert.equal(plain?.isAdmin, true);
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

test("identity paths and banner name the active view", () => {
  assert.equal(usersPath(), "/api/users");
  assert.equal(tenantsPath(), "/api/tenants");
  assert.equal(tenantActionPath(4, "copy"), "/api/tenants/4/copy");
  assert.equal(tenantActionPath(4, "share"), "/api/tenants/4/share");
  assert.equal(impersonatePath(), "/api/auth/impersonate");
  assert.equal(stopImpersonationPath(), "/api/auth/impersonate/stop");
  assert.equal(bannerText("alice"), "正在查看「alice」的视图");
  assert.equal(purposeLabel("production"), "正式");
  assert.equal(purposeLabel("development"), "开发");
  assert.equal(statusLabel("archived"), "已归档");
});

test("API error bodies surface the server detail", () => {
  assert.equal(errorDetail({ detail: "每个用户只能有一个正式租户。" }, "失败"), "每个用户只能有一个正式租户。");
  assert.equal(errorDetail({ detail: [{ msg: "x" }] }, "失败"), "失败");
  assert.equal(errorDetail(null, "失败"), "失败");
});
