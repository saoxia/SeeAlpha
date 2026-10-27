import type { AppView } from "./types";
import { SeeAlphaLogo } from "./brand/seealpha-logo";

const navigationItems: Array<{ id: AppView; label: string }> = [
  { id: "live", label: "LIVE" },
  { id: "dataops", label: "DATA OPS" },
  { id: "data", label: "DATA" },
  { id: "settings", label: "SETTINGS" },
];

type NavigationProps = {
  activeView: AppView;
  onNavigate: (view: AppView) => void;
  username: string;
  onLogout: () => Promise<void>;
};

export function Navigation({ activeView, onNavigate, username, onLogout }: NavigationProps) {
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
        <span>{username}</span>
        <button type="button" onClick={() => void onLogout()}>退出</button>
      </div>
    </header>
  );
}
