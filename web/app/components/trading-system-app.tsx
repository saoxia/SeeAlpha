"use client";

import { useState } from "react";
import { DataManagementPage } from "./data-management/data-management-page";
import { TradePage } from "./trade/trade-page";
import { LiveDashboard } from "./live/live-dashboard";
import { Navigation } from "./navigation";
import { ResearchPage } from "./research/research-page";
import { SettingsPage } from "./settings/settings-page";
import type { AppView } from "./types";
import type { AuthUser } from "./auth/auth-gate";

type TradingSystemAppProps = {
  user: AuthUser;
  onLogout: () => Promise<void>;
};

export function TradingSystemApp({ user, onLogout }: TradingSystemAppProps) {
  const [activeView, setActiveView] = useState<AppView>("live");

  return (
    <main>
      <Navigation
        activeView={activeView}
        onNavigate={setActiveView}
        username={user.username}
        onLogout={onLogout}
      />
      {activeView === "live" && <LiveDashboard />}
      {activeView === "trade" && <TradePage />}
      {activeView === "data" && <DataManagementPage />}
      {activeView === "research" && <ResearchPage />}
      {activeView === "settings" && <SettingsPage />}
    </main>
  );
}
