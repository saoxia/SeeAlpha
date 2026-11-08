"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { createUser, fetchUsers, impersonateUser } from "../../features/identity/api";
import type { AuthSession, ManagedUser } from "../../features/identity/model";

type UserAdminPageProps = {
  currentUsername: string;
  onSession: (session: AuthSession) => void;
  onEntered: () => void;
};

export function UserAdminPage({ currentUsername, onSession, onEntered }: UserAdminPageProps) {
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
    const form = new FormData(event.currentTarget);
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
      event.currentTarget.reset();
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
      onEntered();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法进入该用户的视图。");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="settings-page" id="users">
      <div className="settings-layout">
        <aside className="settings-sidebar" aria-label="用户管理">
          <div className="settings-sidebar-head">
            <span>USERS</span>
            <b>用户管理</b>
          </div>
          <nav>
            <button type="button" className="active" aria-current="page">
              <span>01</span>
              <div>
                <b>用户</b>
                <small>创建并进入视图</small>
              </div>
            </button>
          </nav>
        </aside>
        <main className="settings-content">
          <div className="settings-card">
            <header className="settings-card-head">
              <span>01</span>
              <div>
                <h2>用户</h2>
                <p>新建用户会自动获得一个正式租户。进入视图后，租户操作按该用户的权限执行。</p>
              </div>
            </header>
            {error && <p className="settings-feedback error" role="alert">{error}</p>}
            {notice && <p className="settings-feedback success" role="status">{notice}</p>}
            <form className="form-grid" onSubmit={(event) => void onCreate(event)}>
              <label className="field">
                <span>用户名</span>
                <input name="username" required minLength={3} maxLength={64} autoComplete="off" />
              </label>
              <label className="field">
                <span>密码</span>
                <input name="password" type="password" required minLength={12} maxLength={256} autoComplete="new-password" />
              </label>
              <label className="field">
                <span>确认密码</span>
                <input name="confirm" type="password" required minLength={12} maxLength={256} autoComplete="new-password" />
              </label>
              <div className="field">
                <span>创建</span>
                <button className="monitor-add-button" type="submit" disabled={busy}>
                  {busy ? "正在创建…" : "创建用户"}
                </button>
              </div>
            </form>
            {loading ? <p className="exchange-empty">正在读取用户…</p> : (
              <div className="tenant-list">
                {users.map((user) => (
                  <article key={user.id} className="tenant-row">
                    <div>
                      <b>{user.username}</b>
                      <small>正式租户 · {user.production_tenant_name}</small>
                    </div>
                    <span className="tenant-badge production">正式</span>
                    <span />
                    <div className="tenant-actions">
                      {user.username === currentUsername ? (
                        <small>当前用户</small>
                      ) : (
                        <button type="button" disabled={busy} onClick={() => void enter(user.username)}>
                          进入视图
                        </button>
                      )}
                    </div>
                  </article>
                ))}
              </div>
            )}
          </div>
        </main>
      </div>
    </section>
  );
}
