import type { CoachKey, CoachQuestion } from "@/shared/types/api";

/** India's national cyber-fraud helpline: a real, free number, safe to dial in a demo. */
export const HELPLINE = "1930";
/** "Call my bank" (spec 01 F1): the bank's own fraud line when configured, else the helpline. */
export const BANK_PHONE = import.meta.env.VITE_BANK_PHONE || HELPLINE;

/** Milliseconds left as m:ss, never negative ("9:05", "0:00"). */
export function formatRemaining(ms: number): string {
  const total = Math.max(0, Math.ceil(ms / 1000));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

/** Spec 07 §3: a scammer's power is urgency, so paying waits 10 minutes after the coach opens. */
export const COOLING_OFF_MS = 10 * 60_000;

/** Spec 01 F1: the answer that is a warning sign (YES, except "have you paid them before?"). */
const RED_FLAG: Record<CoachKey, boolean> = {
  on_call_instructed: true,
  threat_or_arrest: true,
  refund_to_receive: true,
  promised_returns: true,
  asked_secrecy: true,
  know_payee: false,
};

export function isCoachKey(key: string): key is CoachKey {
  return key in RED_FLAG;
}

/** The keys answered with a warning sign, in question order. */
export function flaggedKeys(questions: CoachQuestion[], answers: Record<string, boolean>): CoachKey[] {
  return questions.map((q) => q.key).filter((k): k is CoachKey => isCoachKey(k) && answers[k] === RED_FLAG[k]);
}
