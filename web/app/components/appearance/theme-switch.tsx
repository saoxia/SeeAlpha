"use client";

import { useEffect, useRef, useSyncExternalStore } from "react";
import { defaultTheme, readTheme, setTheme, subscribeTheme } from "@/app/features/appearance/theme";

export function ThemeSwitch() {
  const theme = useSyncExternalStore(subscribeTheme, readTheme, () => defaultTheme);
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    function closeOnBackdrop(event: MouseEvent) {
      if (event.target === dialog) dialog?.close();
    }
    // The native dialog handles Escape. A DOM listener is used for backdrop
    // clicks because the dialog element itself is not an interactive control.
    dialog.addEventListener("click", closeOnBackdrop);
    return () => dialog.removeEventListener("click", closeOnBackdrop);
  }, []);

  function selectTheme(next: "classic" | "glass") {
    setTheme(next);
    dialogRef.current?.close();
  }

  return <div className="theme-switch">
    <button
      type="button"
      className="theme-switch-trigger"
      aria-haspopup="dialog"
      onClick={() => dialogRef.current?.showModal()}
    >
      切换主题
    </button>
    <dialog ref={dialogRef} className="theme-picker" aria-label="选择界面主题">
      <div className="theme-picker-content">
        <header className="theme-picker-header">
          <h2>选择界面主题</h2>
          <button type="button" className="theme-picker-close" aria-label="关闭主题选择" onClick={() => dialogRef.current?.close()}>×</button>
        </header>
        <div className="theme-picker-options" role="group" aria-label="可用主题">
          <button type="button" className="theme-picker-option" aria-pressed={theme === "classic"} onClick={() => selectTheme("classic")}>
            <span>像素风</span>
            <span className="theme-picker-selected" aria-hidden="true">{theme === "classic" ? "✓" : ""}</span>
          </button>
          <button type="button" className="theme-picker-option" aria-pressed={theme === "glass"} onClick={() => selectTheme("glass")}>
            <span className="theme-picker-label"><span className="glass-symbol" aria-hidden="true" />液态玻璃</span>
            <span className="theme-picker-selected" aria-hidden="true">{theme === "glass" ? "✓" : ""}</span>
          </button>
        </div>
      </div>
    </dialog>
  </div>;
}
