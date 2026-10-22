import type { Behaviour } from "@/shared/types/api";

const DEVICE_KEY = "fraudshield.deviceId";

/**
 * Random hex. Not crypto.randomUUID(): that exists only on HTTPS or localhost pages, and a phone
 * opens the app over plain http://<laptop-ip>:5173 (T4.5). getRandomValues works everywhere.
 */
export function randomHex(length: number): string {
  const bytes = crypto.getRandomValues(new Uint8Array(Math.ceil(length / 2)));
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("").slice(0, length);
}

const openedAt = Date.now(); // when the app was opened in this tab

/** The customer's usual phone, registered with the bank (scripts/seed.py loads it into Redis). */
export function usualPhoneId(userId: string): string {
  return `D-phone-${userId}`;
}

/** A stable id for this browser as a phone of its own (spec 04 §2.1 `device_id`). */
export function deviceId(): string {
  try {
    const saved = window.localStorage.getItem(DEVICE_KEY);
    if (saved) return saved;
    const id = `D-web-${randomHex(8)}`;
    window.localStorage.setItem(DEVICE_KEY, id);
    return id;
  } catch {
    return "D-web-private";
  }
}

/** Behaviour signals the app can really measure: how long this session has been open. */
export function behaviour(): Behaviour {
  const seconds = Math.round((Date.now() - openedAt) / 1000);
  return { typing_speed_z: 0, session_len_s: seconds, login_to_pay_s: seconds };
}

export function newTxnId(): string {
  return `WEB-${Date.now().toString(36).toUpperCase()}-${randomHex(6).toUpperCase()}`;
}
