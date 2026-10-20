import type { TokenResponse } from "@/shared/types/api";

/**
 * Where the login tokens live (T4.1 decision): the short-lived access token only in memory; the
 * refresh token in localStorage so a reload keeps the session. Refresh tokens rotate (each works
 * once), which limits a stolen one. Moving it to an HttpOnly cookie is a T6.2 follow-up.
 */
const REFRESH_KEY = "fraudshield.refresh_token";

let accessToken: string | null = null;
let accessExpiresAt = 0; // epoch ms
const listeners = new Set<() => void>();

function readStorage(): string | null {
  try {
    return window.localStorage.getItem(REFRESH_KEY);
  } catch {
    return null; // private mode / storage blocked: session lasts until reload
  }
}

function writeStorage(value: string | null): void {
  try {
    if (value === null) window.localStorage.removeItem(REFRESH_KEY);
    else window.localStorage.setItem(REFRESH_KEY, value);
  } catch {
    // ignore: see readStorage
  }
}

export const tokens = {
  get access(): string | null {
    return accessToken;
  },
  get refresh(): string | null {
    return readStorage();
  },
  /** True when the access token expires within `marginMs` (or there is none). */
  expiresSoon(marginMs = 30_000): boolean {
    return accessToken === null || Date.now() > accessExpiresAt - marginMs;
  },
  set(t: TokenResponse): void {
    accessToken = t.access_token;
    accessExpiresAt = Date.now() + t.expires_in * 1000;
    writeStorage(t.refresh_token);
  },
  /** Forget both tokens and tell listeners (the auth provider logs the user out). */
  clear(): void {
    const hadSession = accessToken !== null || readStorage() !== null;
    accessToken = null;
    accessExpiresAt = 0;
    writeStorage(null);
    if (hadSession) listeners.forEach((fn) => fn());
  },
  onCleared(fn: () => void): () => void {
    listeners.add(fn);
    return () => listeners.delete(fn);
  },
};
