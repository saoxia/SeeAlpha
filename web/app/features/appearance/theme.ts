// Keep the existing stored value for the pixel theme so saved preferences continue to work.
export type InterfaceTheme = "classic" | "glass";
export const defaultTheme: InterfaceTheme = "glass";

const storageKey = "seealpha.interface-theme";
const changeEvent = "seealpha:interface-theme";

// A fixed, local preference is applied before paint. No user-supplied script is inserted.
export const themeBootstrap = `try{document.documentElement.dataset.interfaceTheme=localStorage.getItem(${JSON.stringify(storageKey)})==="classic"?"classic":${JSON.stringify(defaultTheme)}}catch{document.documentElement.dataset.interfaceTheme=${JSON.stringify(defaultTheme)}}`;

export function readTheme(): InterfaceTheme {
  return document.documentElement.dataset.interfaceTheme === "classic" ? "classic" : defaultTheme;
}

export function setTheme(theme: InterfaceTheme) {
  document.documentElement.dataset.interfaceTheme = theme;
  try {
    localStorage.setItem(storageKey, theme);
  } catch {
    // Switching still works for this page when the browser blocks persistence.
  }
  window.dispatchEvent(new Event(changeEvent));
}

export function subscribeTheme(onChange: () => void) {
  function syncStorage(event: StorageEvent) {
    if (event.key === storageKey || event.key === null) {
      try {
        if (event.storageArea !== localStorage) return;
      } catch {
        return;
      }
      document.documentElement.dataset.interfaceTheme = event.newValue === "classic" ? "classic" : defaultTheme;
      onChange();
    }
  }
  window.addEventListener(changeEvent, onChange);
  window.addEventListener("storage", syncStorage);
  return () => {
    window.removeEventListener(changeEvent, onChange);
    window.removeEventListener("storage", syncStorage);
  };
}
