"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { createUser, fetchUsers, impersonateUser } from "../../features/identity/api";
import type { AuthSession, ManagedUser } from "../../features/identity/model";

type UserAdminPageProps = {
  currentUsername: string;
  onSession: (session: AuthSession) => void;
};

export function UserAdminPage({ currentUsername, onSession }: UserAdminPageProps) {
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setUsers(await fetchUsers());
      setError("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法读取用户。");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const rows = await fetchUsers();
        if (!active) return;
        setUsers(rows);
        setError("");
      } catch (caught) {
        if (!active) return;
        setError(caught instanceof Error ? caught.message : "无法读取用户。");
      } finally {
        if (active) setLoading(false);
      }
    };
    void load();
    return () => {
      active = false;
    };
  }, []);

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
      setNotice("已创建用户，并自动准备其正式租户。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法创建用户。");
    } finally {
      setBusy(false);
    }
  }

  async function enter(username: string) {
    setBusy(true);
    setError("");
    try {
      onSession(await impersonateUser(username));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法进入该用户的视图。");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="admin-panel admin-users" id="users" aria-label="用户管理">
      <div className="admin-panel-header">
        <div>
          <h2>用户</h2>
          <p>新建用户会自动创建正式租户；进入视图后按该用户的权限操作。</p>
        </div>
        <button type="button" disabled={loading || busy} onClick={() => {
          setLoading(true);
          void refresh();
        }}>刷新列表</button>
      </div>

      {error && <p className="admin-alert" role="alert">{error}</p>}
      {notice && <p className="admin-notice" role="status">{notice}</p>}

      <form className="admin-create-form" onSubmit={(event) => void onCreate(event)}>
        <h3>创建用户</h3>
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
        <p className="admin-hint">密码至少 8 位，同时包含英文字母和数字。</p>
      </form>

      <div className="admin-list-header">
        <h3>用户列表</h3>
        <span>{loading ? "读取中…" : `${users.length} 个用户`}</span>
      </div>
      <div className="admin-table-wrap">
        <table className="admin-users-table">
          <thead>
            <tr><th scope="col">ID</th><th scope="col">用户名</th><th scope="col">正式租户</th><th scope="col">操作</th></tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={4}>正在读取用户…</td></tr>
            ) : users.length === 0 ? (
              <tr><td colSpan={4}>暂无用户。</td></tr>
            ) : users.map((user) => (
              <tr key={user.id}>
                <td>{user.id}</td>
                <td><strong>{user.username}</strong></td>
                <td>{user.production_tenant_name}</td>
                <td>
                  {user.username === currentUsername ? (
                    <span className="admin-muted">当前用户</span>
                  ) : (
                    <button type="button" disabled={busy} onClick={() => void enter(user.username)}>
                      进入视图
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
