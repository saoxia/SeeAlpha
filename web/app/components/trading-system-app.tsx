"use client";

import { useEffect, useState } from "react";
import { DataManagementPage } from "./data-management/data-management-page";
import { TradePage } from "./trade/trade-page";
import { LiveDashboard } from "./live/live-dashboard";
import { Navigation } from "./navigation";
import { ResearchPage } from "./research/research-page";
import { SettingsPage } from "./settings/settings-page";
import { ImpersonationBanner } from "./identity/impersonation-banner";
import { TenantPage } from "./identity/tenant-page";
import { UserAdminPage } from "./identity/user-admin-page";
import type { AppView } from "./types";
import { fetchTenants } from "../features/identity/api";
import { productionTenant, type AuthSession } from "../features/identity/model";

type TradingSystemAppProps = {
  session: AuthSession;
  onSession: (session: AuthSession) => void;
  onLogout: () => Promise<void>;
};

export function TradingSystemApp({ session, onSession, onLogout }: TradingSystemAppProps) {
  const [activeView, setActiveView] = useState<AppView>("live");
  const [productionName, setProductionName] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    const load = async () => {
      try {
        const tenants = await fetchTenants(controller.signal);
        setProductionName(productionTenant(tenants)?.name ?? "");
      } catch (error: unknown) {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setProductionName("");
      }
    };
    void load();
    return () => controller.abort();
  }, [session.user.id, session.impersonating]);

  return (
    <main>
      {session.impersonating && (
        <ImpersonationBanner username={session.user.username} onSession={onSession} />
      )}
      <Navigation
        activeView={activeView}
        onNavigate={setActiveView}
        username={session.user.username}
        onLogout={onLogout}
        isAdmin={session.isAdmin}
        impersonating={session.impersonating}
        productionTenantName={productionName}
      />
      {activeView === "live" && <LiveDashboard />}
      {activeView === "trade" && <TradePage />}
      {activeView === "data" && <DataManagementPage />}
      {activeView === "research" && <ResearchPage />}
      {activeView === "settings" && <SettingsPage />}
      {activeView === "tenants" && <TenantPage />}
      {activeView === "users" && session.isAdmin && !session.impersonating && (
        <UserAdminPage
          currentUsername={session.user.username}
          onSession={onSession}
          onEntered={() => setActiveView("live")}
        />
      )}
    </main>
  );
}
