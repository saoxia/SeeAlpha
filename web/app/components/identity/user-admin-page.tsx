"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { createUser, fetchLegacyTenants, fetchUsers, transferLegacyTenant } from "../../features/identity/api";
import type { LegacyTenant, ManagedUser } from "../../features/identity/model";

export function UserAdminPage() {
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [legacy, setLegacy] = useState<LegacyTenant[]>([]);
  const [targets, setTargets] = useState<Record<number, string>>({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const refresh = useCallback(async () => {
    try {
      const [nextUsers, nextLegacy] = await Promise.all([fetchUsers(), fetchLegacyTenants()]);
      setUsers(nextUsers);
      setLegacy(nextLegacy);
      setError("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法读取用户和历史租户。");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function onCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const username = String(form.get("username") ?? "").trim();
    const password = String(form.get("password") ?? "");
    const confirm = String(form.get("confirm") ?? "");
    if (password !== confirm) {
      setError("两次密码不一致。");
      setNotice("");
      return;
    }
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await createUser({ username, password });
      formElement.reset();
      await refresh();
      setNotice("普通用户已创建，并已分配独立的正式租户。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法创建普通用户。");
    } finally {
      setBusy(false);
    }
  }

  async function transfer(tenant: LegacyTenant) {
    const target = targets[tenant.id];
    if (!target) {
      setError("请选择接收旧租户的普通用户。");
      return;
    }
    if (!window.confirm(
      `确认将旧租户「${tenant.name}」(ID ${tenant.id}) 及其账户、凭据和记录归属变更为「${target}」吗？请先停止相关交易任务。此操作不会复制或删除数据。`,
    )) return;
    setBusy(true);
    setNotice("");
    setError("");
    try {
      await transferLegacyTenant(tenant.id, target);
      await refresh();
      setNotice(`旧租户「${tenant.name}」已转交给「${target}」。`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "迁移失败。");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="admin-panel admin-users" id="users" aria-label="用户管理">
      <div className="admin-panel-header">
        <div>
          <h2>普通用户</h2>
          <p>管理账户与租户。管理员本身不能交易，也不拥有交易租户。</p>
        </div>
        <button type="button" disabled={loading || busy} onClick={() => {
          setLoading(true);
          void refresh();
        }}>刷新列表</button>
      </div>
      {error && <p role="alert" className="admin-alert">{error}</p>}
      {notice && <p role="status" className="admin-notice">{notice}</p>}

      <form className="admin-create-form" onSubmit={(event) => void onCreate(event)}>
        <h3>创建独立普通用户</h3>
        <div className="admin-form-fields">
          <label>
            <span>用户名</span>
            <input name="username" required minLength={3} maxLength={64} autoComplete="off" />
          </label>
          <label>
            <span>密码</span>
            <input name="password" type="password" required minLength={8} maxLength={256}
              pattern="(?=.*[A-Za-z])(?=.*[0-9]).{8,256}"
              title="密码须为 8–256 位，且包含英文字母和数字。" autoComplete="new-password" />
          </label>
          <label>
            <span>确认密码</span>
            <input name="confirm" type="password" required minLength={8} maxLength={256}
              autoComplete="new-password" />
          </label>
          <button type="submit" disabled={busy}>{busy ? "正在创建…" : "创建用户"}</button>
        </div>
        <p className="admin-hint">如需自己交易，请创建单独账户并用其凭据登录业务工作台。</p>
      </form>

      <div className="admin-list-header">
        <h3>用户列表</h3>
        <span>{loading ? "读取中…" : `${users.length} 个普通用户`}</span>
      </div>
      <div className="admin-table-wrap">
        <table className="admin-users-table">
          <thead>
            <tr><th scope="col">ID</th><th scope="col">用户名</th><th scope="col">正式租户</th><th scope="col">身份</th></tr>
          </thead>
          <tbody>
            {loading ? <tr><td colSpan={4}>读取中…</td></tr>
              : users.length === 0 ? <tr><td colSpan={4}>暂无普通用户。</td></tr>
              : users.map((user) => (
                <tr key={user.id}>
                  <td>{user.id}</td>
                  <td><strong>{user.username}</strong></td>
                  <td>{user.production_tenant_name}</td>
                  <td>普通用户</td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>

      {legacy.length > 0 && (
        <div className="admin-legacy-tenants">
          <div className="admin-list-header">
            <h3>待处理的旧管理员租户</h3>
            <span>{legacy.length} 个</span>
          </div>
          <p className="admin-hint">升级不会自动迁移或删除旧交易数据。请先停止相关交易任务，再明确指定接收者；迁移保留租户、账户及关联记录的原 ID。</p>
          <div className="admin-table-wrap">
            <table className="admin-users-table">
              <thead><tr><th scope="col">租户</th><th scope="col">类型</th><th scope="col">账户</th><th scope="col">接收普通用户</th><th scope="col">操作</th></tr></thead>
              <tbody>
                {legacy.map((tenant) => (
                  <tr key={tenant.id}>
                    <td><strong>{tenant.name}</strong><small> · ID {tenant.id}</small></td>
                    <td>{tenant.purpose === "production" ? "正式" : "开发"}</td>
                    <td>{tenant.exchange_accounts} 交易所 / {tenant.virtual_accounts} 模拟</td>
                    <td>
                      <select aria-label={`选择租户 ${tenant.name} 的接收用户`}
                        value={targets[tenant.id] ?? ""}
                        disabled={busy}
                        onChange={(event) => setTargets((current) => ({ ...current, [tenant.id]: event.target.value }))}>
                        <option value="">请选择</option>
                        {users.map((user) => <option key={user.id} value={user.username}>{user.username}</option>)}
                      </select>
                    </td>
                    <td><button type="button" disabled={busy || !targets[tenant.id]} onClick={() => void transfer(tenant)}>转移归属</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </section>
  );
}
