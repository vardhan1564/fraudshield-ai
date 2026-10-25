import type { CoachQuestion, Reason, ScoreResponse } from "@/shared/types/api";

/** What the warning and done screens show; kept for this tab so a reload doesn't lose it. */
export interface PaymentResult {
  txnId: string;
  payee: string;
  amount: number;
  at: string; // ISO time the customer pressed Pay
  decision: ScoreResponse["decision"];
  action: ScoreResponse["action"];
  reasons: Reason[];
  explanation: string;
  explanationPending: boolean;
  /** The customer went ahead after an extra-check warning. */
  continued?: boolean;
  /** Scam Coach (T5.4): the questions to ask, when the coach started (ms), and once answered,
   * the keys answered with a warning sign. */
  coachQuestions?: CoachQuestion[];
  coachStartedAt?: number;
  flagged?: string[];
}

const KEY = "fraudshield.payments";
const KEEP = 20;

function all(): PaymentResult[] {
  try {
    return JSON.parse(window.sessionStorage.getItem(KEY) ?? "[]") as PaymentResult[];
  } catch {
    return [];
  }
}

export function rememberPayment(result: PaymentResult): void {
  try {
    const rest = all().filter((p) => p.txnId !== result.txnId);
    window.sessionStorage.setItem(KEY, JSON.stringify([result, ...rest].slice(0, KEEP)));
  } catch {
    // the screens still get the result from router state
  }
}

export function recallPayment(txnId: string): PaymentResult | null {
  return all().find((p) => p.txnId === txnId) ?? null;
}

export function fromScore(response: ScoreResponse, payee: string, amount: number, at: string): PaymentResult {
  return {
    txnId: response.txn_id,
    payee,
    amount,
    at,
    decision: response.decision,
    action: response.action,
    reasons: response.top_reasons,
    explanation: response.explanation,
    explanationPending: response.explanation_status === "pending",
    coachQuestions: response.coach_questions,
  };
}
