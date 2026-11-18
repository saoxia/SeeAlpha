"use client";

import { useState } from "react";
import { UserAdminPage } from "../identity/user-admin-page";
import { ProjectChecksPage } from "../research/project-checks-page";
import { DataManagementPage } from "../data-management/data-management-page";
import { OlapConnectionSettings } from "../settings/olap-connection-settings";
import { BacktestWorkerSettings } from "./backtest-worker-settings";
import type { AuthSession } from "../../features/identity/model";

type AdminSection = "overview" | "users" | "data" | "olap" | "workers" | "checks";

const sections: Array<{ id: AdminSection; label: string; description: string }> = [
  { id: "overview", label: "概览", description: "常用管理入口" },
  { id: "users", label: "用户管理", description: "创建普通用户、查看正式租户" },
  { id: "data", label: "数据运维", description: "行情库存、账户记录与数据监控" },
  { id: "olap", label: "平台设置", description: "ClickHouse / OLAP 连接配置" },
  { id: "workers", label: "回测 Worker", description: "开发租户回测并发与超时" },
  { id: "checks", label: "项目检查", description: "检查任务、执行记录和日志" },
];

export function AdminConsole({
  session, onLogout,
}: {
  session: AuthSession;
  onLogout: () => Promise<void>;
}) {
  const [section, setSection] = useState<AdminSection>("overview");
  const [loggingOut, setLoggingOut] = useState(false);
  const [logoutError, setLogoutError] = useState("");
  const current = sections.find((item) => item.id === section) ?? sections[0];

  async function logout() {
    setLoggingOut(true);
    setLogoutError("");
    try {
      await onLogout();
    } catch {
      setLogoutError("退出失败，请检查连接后重试。");
    } finally {
      setLoggingOut(false);
    }
  }

  return (
    <main className="admin-console">
      <aside className="admin-sidebar" aria-label="管理后台">
        <div className="admin-sidebar-brand">
          <strong>SeeAlpha</strong>
          <span>管理后台</span>
        </div>
        <nav className="admin-nav" aria-label="管理功能">
          {sections.map((item) => (
            <button
              key={item.id}
              type="button"
              className={section === item.id ? "active" : ""}
              aria-current={section === item.id ? "page" : undefined}
              onClick={() => setSection(item.id)}
            >
              {item.label}
            </button>
          ))}
        </nav>
        <div className="admin-sidebar-footer">
          <span className="admin-identity">{session.user.username} · 管理员</span>
          <button type="button" disabled={loggingOut} onClick={() => void logout()}>
            {loggingOut ? "正在退出…" : "退出登录"}
          </button>
        </div>
      </aside>

      <div className="admin-main">
        <header className="admin-page-header">
          <div>
            <h1>{current.label}</h1>
            <p>{current.description}</p>
          </div>
          <span>管理员专用</span>
        </header>
        {logoutError && <p className="admin-alert" role="alert">{logoutError}</p>}

        <div className="admin-body">
          {section === "overview" && (
            <section className="admin-panel" aria-label="常用管理入口">
              <h2>选择操作</h2>
              <div className="admin-shortcuts">
                {sections.filter((item) => item.id !== "overview").map((item) => (
                  <button key={item.id} type="button" onClick={() => setSection(item.id)}>
                    <strong>{item.label}</strong>
                    <span>{item.description}</span>
                  </button>
                ))}
              </div>
              <p className="admin-hint">管理员只能运维平台。如需交易，请创建独立普通用户账户并使用该账户登录。</p>
            </section>
          )}
          {section === "users" && (
            <UserAdminPage />
          )}
          {section === "data" && <DataManagementPage scope="admin" />}
          {section === "olap" && <section className="settings-page"><OlapConnectionSettings /></section>}
          {section === "workers" && <BacktestWorkerSettings />}
          {section === "checks" && <ProjectChecksPage />}
        </div>
      </div>
    </main>
  );
}
