"use client";

import { useState } from "react";
import Link from "next/link";
import { SeeAlphaLogo } from "../brand/seealpha-logo";
import { ThemeSwitch } from "../appearance/theme-switch";
import { UserAdminPage } from "../identity/user-admin-page";
import { ResearchPage } from "../research/research-page";
import { DataManagementPage } from "../data-management/data-management-page";
import { OlapConnectionSettings } from "../settings/olap-connection-settings";
import type { AuthSession } from "../../features/identity/model";

type AdminSection = "overview" | "users" | "data" | "olap" | "research";

const sections: Array<{ id: AdminSection; label: string }> = [
  { id: "overview", label: "管理概览" },
  { id: "users", label: "用户管理" },
  { id: "data", label: "数据运维" },
  { id: "olap", label: "平台设置" },
  { id: "research", label: "AI Harness" },
];

export function AdminConsole({
  session, onSession, onLogout,
}: {
  session: AuthSession;
  onSession: (session: AuthSession) => void;
  onLogout: () => Promise<void>;
}) {
  const [section, setSection] = useState<AdminSection>("overview");
  const [loggingOut, setLoggingOut] = useState(false);
  const [logoutError, setLogoutError] = useState("");

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
      <header className="admin-topbar">
        <div className="admin-brand">
          <Link href="/admin" aria-label="SeeAlpha 管理后台" onClick={() => setSection("overview")}>
            <SeeAlphaLogo />
          </Link>
          <span>管理后台</span>
        </div>
        <nav aria-label="管理后台导航">
          {sections.map((item) => (
            <button key={item.id} type="button"
              className={section === item.id ? "active" : ""}
              aria-current={section === item.id ? "page" : undefined}
              onClick={() => setSection(item.id)}>
              {item.label}
            </button>
          ))}
        </nav>
        <div className="admin-topbar-actions">
          <span className="admin-identity">{session.user.username} · 管理员</span>
          <Link href="/app" className="admin-workspace-link">进入业务工作台</Link>
          <ThemeSwitch />
          <button type="button" disabled={loggingOut} onClick={() => void logout()}>
            {loggingOut ? "正在退出…" : "退出登录"}
          </button>
        </div>
      </header>
      {logoutError && <p className="admin-error" role="alert">{logoutError}</p>}
      {section === "overview" && (
        <section className="admin-overview" aria-labelledby="admin-overview-title">
          <div className="admin-overview-heading">
            <p>SEEALPHA / CONTROL PLANE</p>
            <h1 id="admin-overview-title">管理后台</h1>
            <span>平台管理与用户交易工作台已分开。管理操作在此进行，日常交易请进入业务工作台。</span>
          </div>
          <div className="admin-overview-cards">
            <button type="button" onClick={() => setSection("users")}>
              <span>01 / IDENTITY</span>
              <strong>用户管理</strong>
              <small>创建用户、查看正式租户归属、进入用户视图</small>
              <b aria-hidden="true">→</b>
            </button>
            <button type="button" onClick={() => setSection("data")}>
              <span>02 / DATA</span>
              <strong>数据运维</strong>
              <small>行情数据管理、运行状态与数据监控</small>
              <b aria-hidden="true">→</b>
            </button>
            <button type="button" onClick={() => setSection("olap")}>
              <span>03 / SETTINGS</span>
              <strong>平台设置</strong>
              <small>配置和测试 OLAP 数据库连接</small>
              <b aria-hidden="true">→</b>
            </button>
            <button type="button" onClick={() => setSection("research")}>
              <span>04 / OPERATIONS</span>
              <strong>AI Harness</strong>
              <small>研究任务、项目检查与运行记录</small>
              <b aria-hidden="true">→</b>
            </button>
          </div>
        </section>
      )}
      {section === "users" && (
        <UserAdminPage currentUsername={session.user.username} onSession={onSession} />
      )}
      {section === "data" && <DataManagementPage scope="admin" />}
      {section === "olap" && <section className="settings-page"><OlapConnectionSettings /></section>}
      {section === "research" && <ResearchPage harnessAccess />}
    </main>
  );
}
