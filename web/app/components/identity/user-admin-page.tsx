"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { createUser, deployUserEngine, fetchEngineNodes, fetchUsers } from "../../features/identity/api";
import type { EngineNodes, ManagedUser } from "../../features/identity/model";

export function UserAdminPage() {
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [engineNodes, setEngineNodes] = useState<EngineNodes>({ deployment: "single", nodes: [] });
  const [selectedNodes, setSelectedNodes] = useState<Record<number, string>>({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const refresh = useCallback(async () => {
    try {
      const [nextUsers, nextNodes] = await Promise.all([fetchUsers(), fetchEngineNodes()]);
      setUsers(nextUsers);
      setEngineNodes(nextNodes);
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
      await refresh();
      setNotice("普通用户及正式租户已创建；Docker 尚未部署，请在用户列表中选择服务器。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法创建普通用户。");
    } finally {
      setBusy(false);
    }
  }

  async function deployEngine(user: ManagedUser) {
    const nodeId = user.engine_node ?? selectedNodes[user.id];
    if (!nodeId) {
      setError("请先为该正式租户选择服务器。");
      return;
    }
    if (!user.engine_node && !window.confirm(
      `确定在「${nodeId === "local" ? "主服务器（本机）" : nodeId}」上创建用户「${user.username}」的正式 Docker 吗？部署后不可直接切换节点。`,
    )) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await deployUserEngine(user.id, nodeId);
      await refresh();
      setNotice(`用户「${user.username}」的正式 Engine 已在 ${nodeId === "local" ? "本机" : nodeId} 部署。`);
    } catch (caught) {
      await refresh();
      setError(caught instanceof Error ? caught.message : "正式 Engine 部署失败。");
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
        <h3>用户列表与正式 Docker</h3>
        <span>{loading ? "读取中…" : `${users.length} 个普通用户`}</span>
      </div>
      <p className="admin-hint">
        创建用户不会自动部署正式 Docker。请先选择服务器，再单独执行部署；部署位置不支持直接变更。
        {engineNodes.deployment === "single" && " 当前 single 模式不支持独立 Docker。"}
      </p>
      <div className="admin-table-wrap">
        <table className="admin-users-table">
          <thead>
            <tr><th scope="col">ID</th><th scope="col">用户名</th><th scope="col">正式租户</th><th scope="col">部署服务器</th><th scope="col">状态</th><th scope="col">操作</th></tr>
          </thead>
          <tbody>
            {loading ? <tr><td colSpan={6}>读取中…</td></tr>
              : users.length === 0 ? <tr><td colSpan={6}>暂无普通用户。</td></tr>
              : users.map((user) => (
                <tr key={user.id}>
                  <td>{user.id}</td>
                  <td><strong>{user.username}</strong></td>
                  <td>{user.production_tenant_name}<small>ID {user.production_tenant_id}</small></td>
                  <td>
                    {user.engine_node ? (user.engine_node === "local" ? "主服务器（本机）" : user.engine_node) : (
                      <select aria-label={`为 ${user.username} 选择 Docker 服务器`}
                        value={selectedNodes[user.id] ?? ""} disabled={busy || loading || engineNodes.deployment === "single"}
                        onChange={(event) => setSelectedNodes((current) => ({ ...current, [user.id]: event.target.value }))}>
                        <option value="">请选择服务器</option>
                        {engineNodes.nodes.map((node) => (
                          <option key={node.id} value={node.id} disabled={!node.online}>
                            {node.id === "local" ? "主服务器（本机）" : node.id}{node.online ? "" : "（离线）"}
                          </option>
                        ))}
                      </select>
                    )}
                  </td>
                  <td>{engineNodes.deployment === "single" ? "单引擎模式"
                    : user.engine_node ? "已选定节点" : "待部署"}</td>
                  <td>
                    <button type="button" disabled={busy || loading || engineNodes.deployment === "single"
                      || (!user.engine_node && !selectedNodes[user.id])}
                      onClick={() => void deployEngine(user)}>
                      {user.engine_node ? "重试部署" : "部署 Docker"}
                    </button>
                  </td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>

    </section>
  );
}
