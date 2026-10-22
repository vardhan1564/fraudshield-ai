import { createContext } from "react";

import type { PaymentContext } from "@/shared/types/api";

/**
 * Spec 07 §1: browsers can't read call state, so the phone's signals come from the Simulate
 * context panel in Settings (stated in the PRD as simulated). Sent with every payment.
 */
export interface SimContext {
  /** The customer's registered phone (seeded a year ago); off = this browser as a brand-new phone. */
  usualPhone: boolean;
  onCall: boolean;
  unknownCaller: boolean; // the caller is not in the customer's contacts
  callMinutes: number;
  screenShare: boolean;
  remoteApp: RemoteApp | null;
  vpn: boolean;
}

export const REMOTE_APPS = ["AnyDesk", "TeamViewer", "QuickSupport"] as const;
export type RemoteApp = (typeof REMOTE_APPS)[number];
export const CALL_MINUTES = [2, 15, 40] as const;

export const NORMAL: SimContext = {
  usualPhone: true,
  onCall: false,
  unknownCaller: false,
  callMinutes: 2,
  screenShare: false,
  remoteApp: null,
  vpn: false,
};

/** One tap for the digital-arrest demo (spec 07 §5): long call from an unknown number + screen share. */
export const SCAM_CALL: SimContext = {
  usualPhone: true, // the victim pays from their own phone
  onCall: true,
  unknownCaller: true,
  callMinutes: 40,
  screenShare: true,
  remoteApp: "AnyDesk",
  vpn: false,
};

export function isActive(sim: SimContext): boolean {
  return !sim.usualPhone || sim.onCall || sim.screenShare || sim.remoteApp !== null || sim.vpn;
}

/** The `context` of POST /transactions/score (spec 04 §2.1). */
export function toPaymentContext(sim: SimContext): PaymentContext {
  return {
    on_call: sim.onCall,
    caller_in_contacts: sim.onCall && !sim.unknownCaller,
    call_duration_s: sim.onCall ? sim.callMinutes * 60 : 0,
    screen_share_active: sim.screenShare,
    remote_access_app: sim.remoteApp,
    is_vpn: sim.vpn,
  };
}

const STORAGE_KEY = "fraudshield.simContext";

export function loadSim(): SimContext {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return NORMAL;
    const saved = JSON.parse(raw) as Partial<SimContext>;
    const remote = REMOTE_APPS.find((a) => a === saved.remoteApp) ?? null;
    const minutes = CALL_MINUTES.find((m) => m === saved.callMinutes) ?? NORMAL.callMinutes;
    return {
      usualPhone: saved.usualPhone !== false,
      onCall: saved.onCall === true,
      unknownCaller: saved.unknownCaller === true,
      callMinutes: minutes,
      screenShare: saved.screenShare === true,
      remoteApp: remote,
      vpn: saved.vpn === true,
    };
  } catch {
    return NORMAL; // private mode or bad data: start normal
  }
}

export function saveSim(sim: SimContext): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(sim));
  } catch {
    // not saved; the panel still works for this visit
  }
}

export interface SimContextValue {
  sim: SimContext;
  update: (change: Partial<SimContext>) => void;
  set: (sim: SimContext) => void;
}

export const SimContextContext = createContext<SimContextValue | null>(null);
