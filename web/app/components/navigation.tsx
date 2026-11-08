"use client";

import { useEffect, useRef, useState } from "react";
import type { AppView } from "./types";
import { SeeAlphaLogo } from "./brand/seealpha-logo";
import { ThemeSwitch } from "./appearance/theme-switch";

const navigationItems: Array<{ id: AppView; label: string }> = [
  { id: "live", label: "LIVE" },
  { id: "trade", label: "TRADE" },
  { id: "data", label: "DATA" },
  { id: "research", label: "RESEARCH" },
  { id: "settings", label: "SETTINGS" },
];

type NavigationProps = {
  activeView: AppView;
  onNavigate: (view: AppView) => void;
  username: string;
  onLogout: () => Promise<void>;
  isAdmin: boolean;
  impersonating: boolean;
  productionTenantName: string;
};

export function Navigation({
  activeView,
  onNavigate,
  username,
  onLogout,
  isAdmin,
  impersonating,
  productionTenantName,
}: NavigationProps) {
  const menuRef = useRef<HTMLDetailsElement>(null);
  const [loggingOut, setLoggingOut] = useState(false);
  const [logoutError, setLogoutError] = useState("");

  useEffect(() => {
    function dismiss(event: PointerEvent) {
      if (event.target instanceof Node && !menuRef.current?.contains(event.target)) {
        menuRef.current?.removeAttribute("open");
      }
    }
    function escape(event: KeyboardEvent) {
      if (event.key === "Escape" && menuRef.current?.open) {
        event.preventDefault();
        menuRef.current.open = false;
        menuRef.current.querySelector<HTMLElement>("summary")?.focus();
      }
    }
    document.addEventListener("pointerdown", dismiss);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", dismiss);
      document.removeEventListener("keydown", escape);
    };
  }, []);

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
    <header className="topbar">
      <button
        type="button"
        className="brand"
        aria-label="返回 SeeAlpha 实时行情"
        onClick={() => onNavigate("live")}
      >
        <SeeAlphaLogo />
      </button>

      <nav aria-label="主导航">
        {navigationItems.map((item) => (
          <button
            key={item.id}
            type="button"
            className={activeView === item.id ? "active" : ""}
            aria-current={activeView === item.id ? "page" : undefined}
            onClick={() => onNavigate(item.id)}
          >
            {item.label}
          </button>
        ))}
      </nav>

      <div className="links">
        <details ref={menuRef} className="user-menu" onBlur={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget)) event.currentTarget.open = false;
        }}>
          <summary aria-label={`用户菜单，${username}`}>
            <span className="user-avatar" aria-hidden="true">{Array.from(username)[0]?.toUpperCase() || "U"}</span>
            <span className="user-name">{username}</span>
            <svg viewBox="0 0 16 16" aria-hidden="true"><path d="m4 6 4 4 4-4" /></svg>
          </summary>
          <div className="user-menu-panel">
            <div className="user-menu-heading">
              <b>{username}</b>
              <span>{impersonating ? "正在查看的用户" : "当前登录账户"}</span>
              {productionTenantName ? <span>正式租户 · {productionTenantName}</span> : null}
            </div>
            <button type="button" className="user-menu-link" onClick={() => {
              menuRef.current?.removeAttribute("open");
              onNavigate("tenants");
            }}>
              租户
            </button>
            {isAdmin && !impersonating && (
              <button type="button" className="user-menu-link" onClick={() => {
                menuRef.current?.removeAttribute("open");
                onNavigate("users");
              }}>
                用户管理
              </button>
            )}
            <section className="user-menu-appearance" aria-label="主题切换">
              <h2>界面外观</h2>
              <ThemeSwitch />
            </section>
            {logoutError && <p role="alert" className="user-menu-error">{logoutError}</p>}
            <button className="user-menu-logout" type="button" disabled={loggingOut} onClick={() => void logout()}>
              <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M8 3H4v14h4M11 6l4 4-4 4M7 10h8" /></svg>
              {loggingOut ? "正在退出…" : "退出登录"}
            </button>
          </div>
        </details>
      </div>
    </header>
  );
}
