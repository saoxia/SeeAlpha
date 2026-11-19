"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { createUser, deployUserEngine, fetchResourceCapabilities, fetchUsers, saveUserResources } from "../../features/identity/api";
import { UserResourcesDialog } from "./user-resources-dialog";
import type { ManagedUser, ResourceCapabilities, UserResources } from "../../features/identity/model";

export function UserAdminPage() {
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [capabilities, setCapabilities] = useState<ResourceCapabilities | null>(null);
  const [editing, setEditing] = useState<ManagedUser | null>(null);
  const [search, setSearch] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const refresh = useCallback(async () => {
    try {
      const [nextUsers, nextCapabilities] = await Promise.all([fetchUsers(), fetchResourceCapabilities()]);
      setUsers(nextUsers);
      setCapabilities(nextCapabilities);
      setError("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法读取用户列表。");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void refresh(), 0);
    return () => window.clearTimeout(timer);
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
      setShowCreate(false);
      await refresh();
      setNotice("普通用户及正式租户已创建。资源分配和正式 Docker 部署请在用户配置中管理。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法创建普通用户。");
    } finally {
      setBusy(false);
    }
  }

  async function saveResources(resources: UserResources) {
    if (!editing) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const updated = await saveUserResources(editing.id, resources);
      setEditing(updated);
      setUsers((current) => current.map((user) => user.id === updated.id ? updated : user));
      setNotice("已保存用户「" + updated.username + "」的资源授权与配额。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "保存资源配置失败。");
    } finally {
      setBusy(false);
    }
  }

  async function deployEngine(nodeId: string) {
    if (!editing) return;
    if (!editing.engine_node && !window.confirm(
      "确定在「" + (nodeId === "local" ? "主服务器（本机）" : nodeId)
      + "」部署用户「" + editing.username + "」的正式 Docker 吗？部署后不能直接切换节点。"
    )) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const updated = await deployUserEngine(editing.id, nodeId);
      setEditing(updated);
      setUsers((current) => current.map((user) => user.id === updated.id ? updated : user));
      setNotice("已提交正式 Docker 部署；绑定节点：" + nodeId + "。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "正式 Engine 部署失败。");
      await refresh();
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
        <div className="admin-users-header-actions">
          <button type="button" disabled={loading || busy}
            onClick={() => setShowCreate((value) => !value)}>
            {showCreate ? "收起" : "＋ 创建用户"}
          </button>
          <button type="button" disabled={loading || busy} onClick={() => {
            setLoading(true);
            void refresh();
          }}>刷新列表</button>
        </div>
      </div>
      {error && <p role="alert" className="admin-alert">{error}</p>}
      {notice && <p role="status" className="admin-notice">{notice}</p>}

      {showCreate && <form className="admin-create-form" onSubmit={(event) => void onCreate(event)}>
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
      </form>}

      <div className="admin-list-header">
        <h3>用户列表</h3>
        <span>{loading ? "读取中…" : users.length + " 个普通用户"}</span>
      </div>
      <p className="admin-hint">资源授权、继承配额与节点设置集中在用户配置中管理。
        正式 Docker 必须显式部署，资源授权不会自动启动或停止已存在的容器。</p>
      <div className="admin-user-filter">
        <input type="search" placeholder="搜索用户名或用户 ID"
          aria-label="搜索用户" value={search} onChange={(event) => setSearch(event.target.value)} />
      </div>
      <div className="admin-table-wrap">
        <table className="admin-users-table">
          <thead><tr>
            <th scope="col">ID</th><th scope="col">用户名</th>
            <th scope="col">正式租户</th><th scope="col">资源授权</th>
            <th scope="col">正式 Docker</th><th scope="col">操作</th>
          </tr></thead>
          <tbody>
            {loading ? <tr><td colSpan={6}>读取中…</td></tr>
              : users.filter((user) => (user.username + " " + user.id).toLowerCase()
                .includes(search.trim().toLowerCase())).length === 0
                ? <tr><td colSpan={6}>未找到用户。</td></tr>
                : users.filter((user) => (user.username + " " + user.id).toLowerCase()
                  .includes(search.trim().toLowerCase())).map((user) => (
                  <tr key={user.id}>
                    <td>{user.id}</td>
                    <td><strong>{user.username}</strong></td>
                    <td>{user.production_tenant_name}<small>ID {user.production_tenant_id}</small></td>
                    <td>
                      <div className="admin-resource-tags">
                        <span className={user.resources.harness.enabled ? "allocated" : "unallocated"}>Harness</span>
                        <span className={user.resources.development.enabled ? "allocated" : "unallocated"}>开发</span>
                        <span className={user.resources.production.enabled ? "allocated" : "unallocated"}>正式</span>
                      </div>
                    </td>
                    <td>{user.engine_node ? "节点已分配：" + user.engine_node : "未部署"}</td>
                    <td>
                      <button type="button" disabled={busy || !capabilities}
                        onClick={() => setEditing(user)}>配置资源</button>
                    </td>
                  </tr>
                ))}
          </tbody>
        </table>
      </div>
      {editing && capabilities && (
        <UserResourcesDialog key={editing.id} user={editing} capabilities={capabilities}
          busy={busy} onClose={() => setEditing(null)}
          onSave={saveResources} onDeploy={deployEngine} />
      )}

    </section>
  );
}
