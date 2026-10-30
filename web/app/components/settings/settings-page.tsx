"use client";

import { useState } from "react";
import { ExchangeAccountSettings } from "./exchange-account-settings";
import { OlapConnectionSettings } from "./olap-connection-settings";
import { AppearanceSettings } from "./appearance-settings";

type SettingsSection = "exchanges" | "olap" | "appearance";

const settingsSections: Array<{
  id: SettingsSection;
  number: string;
  label: string;
  description: string;
}> = [
  { id: "exchanges", number: "01", label: "交易所账户", description: "Binance 与 OKX 多账户" },
  { id: "olap", number: "02", label: "OLAP 连接", description: "行情与分析数据源" },
  { id: "appearance", number: "03", label: "界面外观", description: "像素风 / 液态玻璃" },
];

export function SettingsPage() {
  const [activeSection, setActiveSection] = useState<SettingsSection>("exchanges");

  return (
    <section className="settings-page" id="settings">
      <div className="settings-layout">
        <aside className="settings-sidebar" aria-label="设置目录">
          <div className="settings-sidebar-head">
            <span>SETTINGS</span>
            <b>设置目录</b>
          </div>
          <nav>
            {settingsSections.map((section) => (
              <button
                key={section.id}
                type="button"
                className={activeSection === section.id ? "active" : ""}
                aria-current={activeSection === section.id ? "page" : undefined}
                onClick={() => setActiveSection(section.id)}
              >
                <span>{section.number}</span>
                <div>
                  <b>{section.label}</b>
                  <small>{section.description}</small>
                </div>
                <i aria-hidden="true">→</i>
              </button>
            ))}
          </nav>
        </aside>

        <main className="settings-content">
          {activeSection === "exchanges" && <ExchangeAccountSettings />}
          {activeSection === "olap" && <OlapConnectionSettings />}
          {activeSection === "appearance" && <AppearanceSettings />}
        </main>
      </div>
    </section>
  );
}
