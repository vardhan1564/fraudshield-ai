/**
 * Demo data for the MSW mocks (T4.1): realistic shapes from spec 04, invented values.
 * Deterministic (seeded) so screenshots and tests are stable.
 */
import { DEMO_ACCOUNTS } from "@/shared/lib/demo";
import type {
  AlertItem,
  CaseDetail,
  Channel,
  Decision,
  Me,
  MetricsOverview,
  Reason,
  TxnDetail,
} from "@/shared/types/api";

let seed = 42;
function rand(): number {
  seed = (seed * 1_103_515_245 + 12_345) % 2 ** 31;
  return seed / 2 ** 31;
}
function pick<T>(items: readonly T[]): T {
  return items[Math.floor(rand() * items.length)] as T;
}
function uuid(n: number): string {
  return `00000000-0000-4000-8000-${n.toString(16).padStart(12, "0")}`;
}

export const USERS: (Me & { email: string })[] = DEMO_ACCOUNTS.map((a, i) => ({
  id: uuid(1000 + i),
  name: a.name,
  email: a.email,
  phone: "98xxxxxx21",
  role: a.role,
  language: "en",
}));

const PAYEES = [
  "refund.helpdesk@ybl", "cbi.verification@okaxis", "grocer.mart@okhdfcbank", "fuel.station@oksbi",
  "anita.k@paytm", "rent.owner@okicici", "earn.daily.tasks@paytm", "swiggy.food@ybl",
] as const;
const CATEGORIES = ["P2P", "GROCERY", "FUEL", "FOOD", "RENT", "ECOMMERCE"] as const;

export function reasonsFor(risk: number, onCall = false): Reason[] {
  const all: Reason[] = [
    { feature: "ctx_unknown_call", value: true, impact: 0.25, text: "You are on a call with an unknown number" },
    { feature: "payee_report_count", value: 7, impact: 1.2, text: "Payee reported by 7 users" },
    { feature: "amount_ratio_avg", value: 6.4, impact: 0.9, text: "Amount is 6.4× your usual" },
    { feature: "payee_is_new", value: true, impact: 0.6, text: "First payment to this payee" },
    { feature: "ctx_screen_share", value: true, impact: 0.8, text: "Screen sharing or a remote-access app is on" },
    { feature: "is_night", value: true, impact: 0.3, text: "Late-night payment" },
  ];
  if (risk < 0.02) return [{ feature: "payee_is_new", value: false, impact: 0.01, text: "You have paid this payee before" }];
  return all.filter((r) => onCall || r.feature !== "ctx_unknown_call").slice(0, risk > 0.25 ? 4 : 2);
}

export function decisionFor(risk: number): Decision {
  if (risk > 0.25) return "HOLD";
  if (risk > 0.016) return "STEP_UP";
  return "APPROVE";
}

const customer = USERS.find((u) => u.role === "customer");
const now = Date.now();

export const TRANSACTIONS: TxnDetail[] = Array.from({ length: 48 }, (_, i) => {
  const risk = i % 9 === 0 ? 0.6 + rand() * 0.38 : i % 4 === 0 ? 0.03 + rand() * 0.2 : rand() * 0.015;
  const decision = decisionFor(risk);
  const amount = Math.round((decision === "HOLD" ? 20_000 + rand() * 60_000 : 120 + rand() * 6_000) * 100) / 100;
  const channel: Channel = pick(["UPI", "UPI", "UPI", "CARD", "WALLET"] as const);
  return {
    id: uuid(5000 + i),
    txn_id: `TXN-20261020-${String(1000 + i).padStart(5, "0")}`,
    user_id: customer?.id ?? uuid(1002),
    customer_external_id: `U0${1029 + (i % 7)}`,
    amount,
    currency: "INR",
    channel,
    merchant_category: pick(CATEGORIES),
    payee_vpa: decision === "HOLD" ? pick(PAYEES.slice(0, 2)) : pick(PAYEES.slice(2)),
    risk_score: Math.round(risk * 10_000) / 10_000,
    decision,
    action: decision === "HOLD" ? "ANALYST_REVIEW" : decision === "STEP_UP" ? "OTP" : "NONE",
    model_version: "ensemble-2026-10-18-txn2",
    latency_ms: 9 + Math.round(rand() * 14),
    ts: new Date(now - i * 23 * 60_000).toISOString(),
    model_scores: {
      transaction: Math.min(1, risk * 1.1),
      anomaly: rand() * 0.2,
      graph: 0,
      social_eng: decision === "HOLD" ? 0.7 + rand() * 0.25 : rand() * 0.1,
      meta: risk,
    },
    top_reasons: reasonsFor(risk, decision === "HOLD"),
    explanation:
      decision === "HOLD"
        ? "We paused this payment because it looks like a refund scam. Real banks never ask you to pay to get a refund."
        : decision === "STEP_UP"
          ? "Please confirm this payment with the OTP we sent you."
          : "This payment looks normal.",
    context: { on_call: decision === "HOLD", screen_share_active: false, behaviour: {} },
    device: { fingerprint: "D-88a1", is_rooted: false, first_seen: new Date(now - 90 * 86_400_000).toISOString() },
    ip: "103.21.44.10",
    geo_lat: 19.99,
    geo_lon: 73.79,
  };
});

export const CASES: CaseDetail[] = TRANSACTIONS.filter((t) => t.decision === "HOLD").map((t, i) => ({
  case_id: uuid(8000 + i),
  alert_id: uuid(7000 + i),
  severity: (t.risk_score ?? 0) >= 0.9 ? "high" : "medium",
  status: i === 0 ? "assigned" : "open",
  assigned_to: i === 0 ? (USERS[0]?.id ?? null) : null,
  label: null,
  notes: null,
  resolved_by: null,
  resolved_at: null,
  created_at: t.ts,
  transaction: {
    id: t.id,
    txn_id: t.txn_id,
    amount: t.amount,
    currency: t.currency,
    channel: t.channel,
    merchant_category: t.merchant_category,
    payee_vpa: t.payee_vpa,
    risk_score: t.risk_score,
    decision: t.decision,
    action: t.action,
    model_version: t.model_version,
    ts: t.ts,
    customer: { id: t.user_id, external_id: t.customer_external_id, name: "Ravi Kumar", phone: "98xxxxxx21" },
  },
  top_reasons: t.top_reasons,
  explanation: t.explanation,
}));

export function alertOf(c: CaseDetail): AlertItem {
  return {
    alert_id: c.alert_id,
    case_id: c.case_id,
    txn_id: c.transaction.txn_id,
    severity: c.severity,
    status: c.status,
    assigned_to: c.assigned_to,
    risk_score: c.transaction.risk_score,
    decision: c.transaction.decision,
    amount: c.transaction.amount,
    channel: c.transaction.channel,
    top_reasons: c.top_reasons,
    created_at: c.created_at,
  };
}

export function metrics(): MetricsOverview {
  return {
    date_from: new Date(now - 86_400_000).toISOString(),
    date_to: new Date(now).toISOString(),
    txn_count: 12_480,
    fraud_caught: 214,
    amount_saved_inr: 3_845_200,
    false_positive_rate: 0.0064,
    p95_latency_ms: 34,
    by_hour: Array.from({ length: 24 }, (_, hour) => {
      const total = Math.round(180 + 420 * Math.sin(((hour - 6) / 24) * Math.PI) ** 2 + rand() * 60);
      return { hour, total, flagged: Math.round(total * (hour < 5 || hour > 22 ? 0.04 : 0.012)) };
    }),
    by_type: [
      { scenario: "digital_arrest", count: 61 },
      { scenario: "fake_support_refund", count: 54 },
      { scenario: "investment_task", count: 39 },
      { scenario: "account_takeover", count: 33 },
      { scenario: "card_testing", count: 27 },
    ],
    by_city: [
      { city: "Mumbai", flagged: 48 }, { city: "Pune", flagged: 31 }, { city: "Delhi", flagged: 29 },
      { city: "Bengaluru", flagged: 24 }, { city: "Lucknow", flagged: 15 },
    ],
  };
}
