import { useSyncExternalStore } from "react";

export type Theme = "light" | "dark";
const STORAGE_KEY = "fraudshield.theme";

function initialTheme(): Theme {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    // storage blocked: fall back to the OS setting
  }
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

// One theme for the whole app (every toggle and every reader share it).
let current: Theme = initialTheme();
const listeners = new Set<() => void>();
document.documentElement.classList.toggle("dark", current === "dark");

function setTheme(next: Theme): void {
  current = next;
  document.documentElement.classList.toggle("dark", next === "dark");
  try {
    window.localStorage.setItem(STORAGE_KEY, next);
  } catch {
    // keep it for this visit only
  }
  listeners.forEach((fn) => fn());
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/** Light/dark theme (spec 06 §2): the OS setting by default, remembered once chosen. */
export function useTheme(): { theme: Theme; toggle: () => void } {
  const theme = useSyncExternalStore(subscribe, () => current);
  return { theme, toggle: () => setTheme(theme === "dark" ? "light" : "dark") };
}
