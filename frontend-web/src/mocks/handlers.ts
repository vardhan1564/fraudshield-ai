/**
 * MSW handlers for the P0 endpoints (spec 04), so the UI runs without the backend
 * (`npm run dev:mock`) and tests have a stable API. Same paths, bodies and error shape as the
 * real API, including refresh-token rotation (a reused refresh token is a 401).
 */
import { http, HttpResponse, ws } from "msw";

import { alertOf, CASES, decisionFor, metrics, reasonsFor, TRANSACTIONS, USERS } from "@/mocks/data";
import { API_URL } from "@/shared/api/client";
import { WS_URL } from "@/shared/api/ws";
import { DEMO_PASSWORD } from "@/shared/lib/demo";
import type {
  AlertCreatedMessage,
  CaseUpdate,
  ExplainResponse,
  LoginRequest,
  Page,
  PayeeReputation,
  ReportCreate,
  ReportCreated,
  ScoreRequest,
  ScoreResponse,
  TokenResponse,
  TxnDetail,
  TxnItem,
} from "@/shared/types/api";

const url = (path: string) => `${API_URL}${path}`;
const ACCESS_TTL_S = 1800;

function apiError(status: number, code: string, message: string) {
  return HttpResponse.json({ error: { code, message, details: {} } }, { status });
}

// --- tokens ---------------------------------------------------------------------------------

let counter = 0;
const refreshOwner = new Map<string, string>(); // live refresh token -> email
const accessOwner = new Map<string, string>(); // access token -> email

function issue(email: string): TokenResponse {
  counter += 1;
  const access = `mock-access-${counter}`;
  const refresh = `mock-refresh-${counter}`;
  accessOwner.set(access, email);
  refreshOwner.set(refresh, email);
  return { access_token: access, refresh_token: refresh, token_type: "bearer", expires_in: ACCESS_TTL_S };
}

function userOf(request: Request) {
  const token = request.headers.get("Authorization")?.replace(/^Bearer /, "") ?? "";
  const email = accessOwner.get(token);
  return USERS.find((u) => u.email === email) ?? null;
}

/** For tests: forget all access tokens (as if they expired). */
export function expireAccessTokens(): void {
  accessOwner.clear();
}

function toItem(t: TxnDetail): TxnItem {
  return {
    id: t.id, txn_id: t.txn_id, user_id: t.user_id, customer_external_id: t.customer_external_id,
    amount: t.amount, currency: t.currency, channel: t.channel, merchant_category: t.merchant_category,
    payee_vpa: t.payee_vpa, risk_score: t.risk_score, decision: t.decision, action: t.action,
    model_version: t.model_version, latency_ms: t.latency_ms, ts: t.ts,
  };
}

function page<T>(items: T[], request: Request): Page<T> {
  const params = new URL(request.url).searchParams;
  const pageNo = Number(params.get("page") ?? "1");
  const size = Number(params.get("page_size") ?? "50");
  return { items: items.slice((pageNo - 1) * size, pageNo * size), page: pageNo, page_size: size, total: items.length };
}

function needUser(request: Request, roles?: string[]) {
  const user = userOf(request);
  if (!user) return { error: apiError(401, "UNAUTHORIZED", "Invalid token") };
  if (roles && !roles.includes(user.role)) return { error: apiError(403, "FORBIDDEN", "Your role cannot do this") };
  return { user };
}

// --- live updates -----------------------------------------------------------------------------

const alertsLink = ws.link(`${WS_URL}/alerts`);
const customerLink = ws.link(`${WS_URL}/customer`);

export const handlers = [
  http.post(url("/auth/login"), async ({ request }) => {
    const body = (await request.json()) as LoginRequest;
    const user = USERS.find((u) => u.email === body.email.toLowerCase());
    if (!user || body.password !== DEMO_PASSWORD) {
      return apiError(401, "UNAUTHORIZED", "Wrong email or password");
    }
    return HttpResponse.json(issue(user.email));
  }),

  http.post(url("/auth/refresh"), async ({ request }) => {
    const { refresh_token } = (await request.json()) as { refresh_token: string };
    const email = refreshOwner.get(refresh_token);
    if (!email) return apiError(401, "UNAUTHORIZED", "Invalid token");
    refreshOwner.delete(refresh_token); // rotation: each refresh token works once
    return HttpResponse.json(issue(email));
  }),

  http.get(url("/auth/me"), ({ request }) => {
    const user = userOf(request);
    return user ? HttpResponse.json(user) : apiError(401, "UNAUTHORIZED", "Invalid token");
  }),

  http.post(url("/transactions/score"), async ({ request }) => {
    const auth = needUser(request, ["customer", "partner"]);
    if (auth.error) return auth.error;
    const body = (await request.json()) as ScoreRequest;
    const onCall = Boolean(body.context?.on_call && !body.context.caller_in_contacts);
    const risk = Math.min(0.99, body.amount / 100_000 + (onCall ? 0.3 : 0) + (body.context?.screen_share_active ? 0.25 : 0));
    const decision = decisionFor(risk);
    const response: ScoreResponse = {
      txn_id: body.txn_id,
      risk_score: Math.round(risk * 10_000) / 10_000,
      decision,
      action: decision === "HOLD" ? "ANALYST_REVIEW" : decision === "STEP_UP" ? (onCall ? "SCAM_COACH" : "OTP") : "NONE",
      model_scores: { transaction: risk, anomaly: 0.05, graph: 0, social_eng: onCall ? 0.9 : 0.05 },
      top_reasons: reasonsFor(risk, onCall),
      explanation: decision === "APPROVE" ? "This payment looks normal." : "Before you pay, please answer a few quick questions.",
      explanation_status: decision === "APPROVE" ? "ready" : "pending",
      coach_questions: onCall
        ? [
            { key: "on_call_instructed", text: "Is someone on a call telling you to make this payment?" },
            { key: "refund_to_receive", text: "Are you paying to receive a refund, prize or cashback?" },
          ]
        : [],
      model_version: "ensemble-mock",
      degraded: ["graph"],
      latency_ms: 12,
    };
    if (response.explanation_status === "pending") {
      setTimeout(() => {
        customerLink.broadcast(
          JSON.stringify({
            type: "explanation.ready",
            data: { txn_id: body.txn_id, explanation: "This looks like a scam: real banks and police never ask you to pay over a call." },
          }),
        );
      }, 1500);
    }
    return HttpResponse.json(response);
  }),

  http.get(url("/transactions"), ({ request }) => {
    const auth = needUser(request, ["analyst", "admin"]);
    if (auth.error) return auth.error;
    const decision = new URL(request.url).searchParams.get("decision");
    const items = TRANSACTIONS.filter((t) => !decision || t.decision === decision).map(toItem);
    return HttpResponse.json(page(items, request));
  }),

  http.get(url("/transactions/:ref/explain"), ({ request, params }) => {
    const auth = needUser(request);
    if (auth.error) return auth.error;
    const txn = TRANSACTIONS.find((t) => t.txn_id === params.ref || t.id === params.ref);
    if (!txn) return apiError(404, "NOT_FOUND", "Transaction not found");
    const body: ExplainResponse = { txn_id: txn.txn_id, shap: txn.top_reasons, explanation: txn.explanation ?? "", language: "en" };
    return HttpResponse.json(body);
  }),

  http.get(url("/transactions/:ref"), ({ request, params }) => {
    const auth = needUser(request);
    if (auth.error) return auth.error;
    const txn = TRANSACTIONS.find((t) => t.txn_id === params.ref || t.id === params.ref);
    return txn ? HttpResponse.json(txn) : apiError(404, "NOT_FOUND", "Transaction not found");
  }),

  http.get(url("/alerts"), ({ request }) => {
    const auth = needUser(request, ["analyst"]);
    if (auth.error) return auth.error;
    return HttpResponse.json(page(CASES.filter((c) => c.status !== "closed").map(alertOf), request));
  }),

  http.get(url("/cases/:id"), ({ request, params }) => {
    const auth = needUser(request, ["analyst"]);
    if (auth.error) return auth.error;
    const found = CASES.find((c) => c.case_id === params.id);
    return found ? HttpResponse.json(found) : apiError(404, "NOT_FOUND", "Case not found");
  }),

  http.patch(url("/cases/:id"), async ({ request, params }) => {
    const auth = needUser(request, ["analyst"]);
    if (auth.error) return auth.error;
    const found = CASES.find((c) => c.case_id === params.id);
    if (!found) return apiError(404, "NOT_FOUND", "Case not found");
    const body = (await request.json()) as CaseUpdate;
    if (body.status === "closed" && !(body.label ?? found.label)) {
      return apiError(400, "VALIDATION_ERROR", "Label the case (fraud or legit) before closing");
    }
    Object.assign(found, {
      ...(body.status && { status: body.status }),
      ...(body.label && { label: body.label, resolved_by: auth.user.id, resolved_at: new Date().toISOString() }),
      ...(body.notes !== undefined && { notes: body.notes }),
      ...("assigned_to" in body && { assigned_to: body.assigned_to }),
    });
    return HttpResponse.json(found);
  }),

  http.post(url("/reports"), async ({ request }) => {
    const auth = needUser(request, ["customer"]);
    if (auth.error) return auth.error;
    const body = (await request.json()) as ReportCreate;
    const created: ReportCreated = {
      report_id: crypto.randomUUID(),
      payee_vpa: body.payee_vpa.toLowerCase(),
      report_count: 1,
      reputation_score: 0.1,
      risk_level: "low",
    };
    return HttpResponse.json(created, { status: 201 });
  }),

  http.get(url("/payees/:vpa/reputation"), ({ request, params }) => {
    const auth = needUser(request);
    if (auth.error) return auth.error;
    const vpa = String(params.vpa);
    const scam = vpa.includes("refund") || vpa.includes("cbi") || vpa.includes("earn");
    const body: PayeeReputation = {
      vpa,
      known: true,
      reputation_score: scam ? 0.8 : 0,
      risk_level: scam ? "high" : "low",
      report_count: scam ? 8 : 0,
      confirmed_fraud_count: 0,
      graph_flag: false,
      account_age_days: scam ? 12 : 1460,
      reports_by_type: scam ? { fake_support_refund: 8 } : {},
      recent_reports: auth.user.role === "customer" ? null : [],
    };
    return HttpResponse.json(body);
  }),

  http.get(url("/metrics/overview"), ({ request }) => {
    const auth = needUser(request, ["analyst", "admin"]);
    if (auth.error) return auth.error;
    return HttpResponse.json(metrics());
  }),

  alertsLink.addEventListener("connection", ({ client }) => {
    // A new HOLD every 15 s, like the worker's alert.created.
    const timer = setInterval(() => {
      const c = CASES[Math.floor(Math.random() * CASES.length)];
      if (!c) return;
      const message: AlertCreatedMessage = {
        type: "alert.created",
        data: {
          alert_id: c.alert_id,
          case_id: c.case_id,
          txn_id: c.transaction.txn_id,
          risk_score: c.transaction.risk_score ?? 0.8,
          decision: "HOLD",
          top_reasons: c.top_reasons,
          created_at: new Date().toISOString(),
        },
      };
      client.send(JSON.stringify(message));
    }, 15_000);
    client.addEventListener("close", () => clearInterval(timer));
  }),

  customerLink.addEventListener("connection", () => undefined),
];
