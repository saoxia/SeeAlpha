"use client";

import { useSyncExternalStore } from "react";
import { defaultTheme, readTheme, setTheme, subscribeTheme } from "@/app/features/appearance/theme";

export function ThemeSwitch() {
  const theme = useSyncExternalStore(subscribeTheme, readTheme, () => defaultTheme);

  return <div className="interface-switch" role="group" aria-label="界面版本">
    <button type="button" aria-pressed={theme === "classic"} onClick={() => setTheme("classic")}>
      像素风
    </button>
    <button type="button" aria-pressed={theme === "glass"} onClick={() => setTheme("glass")}>
      <span className="glass-symbol" aria-hidden="true" />液态玻璃
    </button>
  </div>;
}
