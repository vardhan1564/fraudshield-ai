/**
 * MSW handlers for the P0 endpoints (spec 04), so the UI runs without the backend
 * (`npm run dev:mock`) and tests have a stable API. Same paths, bodies and error shape as the
 * real API, including refresh-token rotation (a reused refresh token is a 401).
 */
import { http, HttpResponse, ws } from "msw";

import {
  alertOf,
  CASES,
  decisionFor,
  metrics,
  newPayment,
  reasonsFor,
  shapFor,
  TRANSACTIONS,
  USERS,
} from "@/mocks/data";
import { neighbourhood, ringPage } from "@/mocks/graph";
import { fieldOf, MERCHANT_CATEGORIES, opsFor } from "@/dashboard/lib/rules";
import { API_URL } from "@/shared/api/client";
import { WS_URL } from "@/shared/api/ws";
import { DEMO_PASSWORD } from "@/shared/lib/demo";
import type {
  AlertCreatedMessage,
  AuditEntry,
  ModelVersion,
  Rule,
  RuleClause,
  RuleIn,
  CaseUpdate,
  ExplainResponse,
  LoginRequest,
  Me,
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
    model_version: t.model_version, latency_ms: t.latency_ms, ts: t.ts, case_id: t.case_id,
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

// --- payee reports (spec 01 F7) -----------------------------------------------------------------

const reporters = new Map<string, Set<string>>(); // payee VPA -> customers who reported it here

function isScamVpa(vpa: string): boolean {
  return vpa.includes("refund") || vpa.includes("cbi") || vpa.includes("earn");
}

/** Demo scam payees start with 8 reports; each customer can add one more (as the real API). */
function reportCount(vpa: string): number {
  return (isScamVpa(vpa) ? 8 : 0) + (reporters.get(vpa)?.size ?? 0);
}

function reputationOf(count: number) {
  const score = Math.min(1, Math.round(0.1 * count * 10_000) / 10_000);
  return { reputation_score: score, risk_level: score > 0.7 ? "high" : score >= 0.3 ? "medium" : "low" } as const;
}

// --- admin: rules, models, audit log (T4.7) -------------------------------------------------------

const DEMO_TIME = new Date(Date.now() - 3 * 86_400_000).toISOString();
const ADMIN = USERS.find((u) => u.role === "admin");
const ANALYST = USERS.find((u) => u.role === "analyst");
const CUSTOMER = USERS.find((u) => u.role === "customer");

const RULES: Rule[] = [
  { name: "Large payment to a new payee", condition_json: { all: [{ field: "amount", op: ">=", value: 50_000 }, { field: "payee_is_new", op: "==", value: true }] }, action: "STEP_UP", priority: 10 },
  { name: "Reported payee", condition_json: { all: [{ field: "payee_report_count", op: ">=", value: 3 }] }, action: "STEP_UP", priority: 20 },
  { name: "Unknown caller and a new payee", condition_json: { all: [{ field: "ctx_unknown_call", op: "==", value: true }, { field: "payee_is_new", op: "==", value: true }] }, action: "HOLD", priority: 5 },
  { name: "Night-time burst", condition_json: { all: [{ field: "is_night", op: "==", value: true }, { field: "txn_count_1h", op: ">=", value: 5 }] }, action: "STEP_UP", priority: 50 },
  { name: "Rooted phone, big amount", condition_json: { any: [{ field: "device_rooted", op: "==", value: true }] }, action: "STEP_UP", priority: 100, is_active: false },
].map((r, i) => ({ is_active: true, ...r, id: `00000000-0000-4000-9000-${String(i + 1).padStart(12, "0")}`, updated_by: ADMIN?.id ?? null, created_at: DEMO_TIME }) as Rule);

const MODELS: ModelVersion[] = [
  { id: "00000000-0000-4000-a000-000000000001", name: "ensemble", version: "ensemble-2026-10-18-txn2", mlflow_run_id: "fb9a87d9aa0c4531a87b46fe9b8b05be",
    metrics_json: { hold: 0.25, step_up: 0.016, txn_model: { test: { pr_auc: 0.9968, roc_auc: 0.9999, recall_at_1pct_fpr: 1 } }, validation_out_of_fold: { recall_hold: 0.862, fpr_hold: 0.0054 } },
    is_active: true, loaded: true, deployed_at: DEMO_TIME, created_at: DEMO_TIME },
  { id: "00000000-0000-4000-a000-000000000002", name: "ensemble", version: "ensemble-2026-10-15-txn1", mlflow_run_id: "1c0ffee0000000000000000000000001",
    metrics_json: { hold: 0.3, step_up: 0.02, txn_model: { test: { pr_auc: 0.9994, roc_auc: 0.9999, recall_at_1pct_fpr: 1 } } },
    is_active: false, loaded: false, deployed_at: null, created_at: new Date(Date.now() - 7 * 86_400_000).toISOString() },
];

let auditId = 0;
const AUDIT: AuditEntry[] = [];
function audit(actor: string | null, action: string, entity: string, entityId: string | null, before: Record<string, unknown> | null, after: Record<string, unknown> | null, minutesAgo = 0) {
  auditId += 1;
  AUDIT.unshift({ id: auditId, ts: new Date(Date.now() - minutesAgo * 60_000).toISOString(), actor_id: actor, action, entity, entity_id: entityId, ip: "127.0.0.1", before_json: before, after_json: after });
}
audit(CUSTOMER?.id ?? null, "auth.login", "user", CUSTOMER?.id ?? null, null, null, 300);
audit(CUSTOMER?.id ?? null, "report.create", "report", "00000000-0000-4000-b000-000000000001", null, null, 240);
audit(ANALYST?.id ?? null, "auth.login", "user", ANALYST?.id ?? null, null, null, 120);
audit(ANALYST?.id ?? null, "case.update", "case", "00000000-0000-4000-8000-000000008000", { status: "open", label: null }, { status: "closed", label: "fraud" }, 90);
audit(ADMIN?.id ?? null, "auth.login", "user", ADMIN?.id ?? null, null, null, 30);

/** The API's check_condition (spec 05 rule format), using the form's own feature list. */
function conditionError(condition: unknown): string | null {
  if (!condition || typeof condition !== "object") return "condition_json must be an object";
  const keys = Object.keys(condition);
  if (keys.length !== 1 || (keys[0] !== "all" && keys[0] !== "any")) return 'use exactly one of "all" or "any"';
  const clauses = (condition as Record<string, unknown>)[keys[0]];
  if (!Array.isArray(clauses) || clauses.length < 1 || clauses.length > 20) return "needs 1 to 20 clauses";
  for (const [i, c] of (clauses as RuleClause[]).entries()) {
    const field = fieldOf(c.field);
    if (!field) return `clause ${i + 1}: unknown feature '${c.field}'`;
    if (!opsFor(field).includes(c.op)) return `clause ${i + 1}: ${c.field} needs == or !=`;
    if (field.type === "bool" && typeof c.value !== "boolean") return `clause ${i + 1}: ${c.field} is true/false`;
    if (field.type === "category" && !(MERCHANT_CATEGORIES as readonly unknown[]).includes(c.value)) return `clause ${i + 1}: unknown category`;
    if (field.type === "number" && typeof c.value !== "number") return `clause ${i + 1}: ${c.field} needs a number`;
  }
  return null;
}

function ruleBodyError(body: RuleIn, id?: string) {
  const problem = conditionError(body.condition_json);
  if (problem) return apiError(400, "VALIDATION_ERROR", problem);
  if (RULES.some((r) => r.name === body.name.trim() && r.id !== id)) return apiError(409, "RULE_NAME_TAKEN", "A rule with this name already exists");
  return null;
}

function ruleState(r: Rule) {
  return { name: r.name, condition_json: r.condition_json, action: r.action, priority: r.priority, is_active: r.is_active };
}

function auditCsv(items: AuditEntry[]): string {
  const cell = (v: unknown) => {
    const text = v === null || v === undefined ? "" : typeof v === "object" ? JSON.stringify(v) : String(v);
    return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  };
  const head = "id,ts,actor_id,action,entity,entity_id,ip,before_json,after_json";
  return [head, ...items.map((a) => [a.id, a.ts, a.actor_id, a.action, a.entity, a.entity_id, a.ip, a.before_json, a.after_json].map(cell).join(","))].join("\n") + "\n";
}

const adminHandlers = [
  http.get(url("/rules"), ({ request }) => {
    const auth = needUser(request, ["admin"]);
    if (auth.error) return auth.error;
    return HttpResponse.json(page(RULES, request));
  }),
  http.post(url("/rules"), async ({ request }) => {
    const auth = needUser(request, ["admin"]);
    if (auth.error) return auth.error;
    const body = (await request.json()) as RuleIn;
    const error = ruleBodyError(body);
    if (error) return error;
    const rule: Rule = { name: body.name.trim(), condition_json: body.condition_json, action: body.action, priority: body.priority ?? 100,
      is_active: body.is_active ?? true, id: crypto.randomUUID(), updated_by: auth.user.id, created_at: new Date().toISOString() };
    RULES.push(rule);
    audit(auth.user.id, "rule.create", "rule", rule.id, null, ruleState(rule));
    return HttpResponse.json(rule, { status: 201 });
  }),
  http.put(url("/rules/:id"), async ({ request, params }) => {
    const auth = needUser(request, ["admin"]);
    if (auth.error) return auth.error;
    const found = RULES.find((r) => r.id === params.id);
    if (!found) return apiError(404, "NOT_FOUND", "Rule not found");
    const body = (await request.json()) as RuleIn;
    const error = ruleBodyError(body, found.id);
    if (error) return error;
    const before = ruleState(found);
    Object.assign(found, { name: body.name.trim(), condition_json: body.condition_json, action: body.action, priority: body.priority ?? 100, is_active: body.is_active ?? true, updated_by: auth.user.id });
    audit(auth.user.id, "rule.update", "rule", found.id, before, ruleState(found));
    return HttpResponse.json(found);
  }),
  http.delete(url("/rules/:id"), ({ request, params }) => {
    const auth = needUser(request, ["admin"]);
    if (auth.error) return auth.error;
    const index = RULES.findIndex((r) => r.id === params.id);
    if (index < 0) return apiError(404, "NOT_FOUND", "Rule not found");
    const [gone] = RULES.splice(index, 1);
    if (gone) audit(auth.user.id, "rule.delete", "rule", gone.id, ruleState(gone), null);
    return new HttpResponse(null, { status: 204 });
  }),
  http.get(url("/models"), ({ request }) => {
    const auth = needUser(request, ["admin"]);
    if (auth.error) return auth.error;
    return HttpResponse.json(page(MODELS, request));
  }),
  http.post(url("/models/:version/deploy"), ({ request, params }) => {
    const auth = needUser(request, ["admin"]);
    if (auth.error) return auth.error;
    const found = MODELS.find((m) => m.version === params.version);
    if (!found) return apiError(404, "NOT_FOUND", "Unknown model version");
    if (!found.loaded) return apiError(409, "MODEL_NOT_LOADED", "Only the loaded model can be deployed until hot reload (T5.8)");
    for (const m of MODELS) m.is_active = m === found;
    found.deployed_at = new Date().toISOString();
    audit(auth.user.id, "model.deploy", "model_version", found.id, null, { version: found.version });
    return HttpResponse.json(found);
  }),
  http.get(url("/audit-log"), ({ request }) => {
    const auth = needUser(request, ["admin"]);
    if (auth.error) return auth.error;
    const q = new URL(request.url).searchParams;
    const from = q.get("from");
    const to = q.get("to");
    const items = AUDIT.filter((a) => (!q.get("entity") || a.entity === q.get("entity")) && (!q.get("action") || a.action === q.get("action"))
      && (!q.get("actor") || a.actor_id === q.get("actor")) && (!from || Date.parse(a.ts) >= Date.parse(from)) && (!to || Date.parse(a.ts) < Date.parse(to)));
    if (q.get("format") === "csv") {
      return new HttpResponse(auditCsv(items), { headers: { "Content-Type": "text/csv; charset=utf-8", "Content-Disposition": 'attachment; filename="audit-log.csv"' } });
    }
    return HttpResponse.json(page(items, request));
  }),
];

/** For tests: the mock's rules, as the API holds them. */
export function mockRules(): readonly Rule[] {
  return RULES;
}

// --- live updates -----------------------------------------------------------------------------

export const alertsLink = ws.link(`${WS_URL}/alerts`);
export const customerLink = ws.link(`${WS_URL}/customer`);

/** The worker's `alert.created` for a HOLD payment (spec 04 §3). */
export function alertCreated(txn: TxnDetail): AlertCreatedMessage {
  const found = CASES.find((c) => c.transaction.id === txn.id);
  return {
    type: "alert.created",
    data: {
      alert_id: found?.alert_id ?? "",
      case_id: found?.case_id ?? "",
      txn_id: txn.txn_id,
      risk_score: txn.risk_score ?? 0.8,
      decision: "HOLD",
      top_reasons: txn.top_reasons,
      created_at: found?.created_at ?? txn.ts,
    },
  };
}

export const handlers = [
  ...adminHandlers,
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

  http.patch(url("/auth/me"), async ({ request }) => {
    const user = userOf(request);
    if (!user) return apiError(401, "UNAUTHORIZED", "Invalid token");
    const body = (await request.json()) as { language: Me["language"] };
    user.language = body.language;
    return HttpResponse.json(user);
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
    const auth = needUser(request, ["analyst", "admin", "customer"]);
    if (auth.error) return auth.error;
    const params = new URL(request.url).searchParams;
    const decision = params.get("decision");
    const from = params.get("from");
    const payeeVpa = params.get("payee_vpa")?.trim().toLowerCase();
    // customers (and mine=true) only ever get their own payments (T4.5)
    const userId = auth.user.role === "customer" || params.get("mine") === "true" ? auth.user.id : params.get("user_id");
    const items = TRANSACTIONS.filter((t) => !decision || t.decision === decision)
      .filter((t) => !userId || t.user_id === userId || t.customer_external_id === userId)
      .filter((t) => !from || Date.parse(t.ts) >= Date.parse(from))
      .filter((t) => !payeeVpa || t.payee_vpa === payeeVpa)
      .sort((a, b) => Date.parse(b.ts) - Date.parse(a.ts))
      .map(toItem);
    return HttpResponse.json(page(items, request));
  }),

  http.get(url("/transactions/:ref/explain"), ({ request, params }) => {
    const auth = needUser(request);
    if (auth.error) return auth.error;
    const txn = TRANSACTIONS.find((t) => t.txn_id === params.ref || t.id === params.ref);
    if (!txn) return apiError(404, "NOT_FOUND", "Transaction not found");
    const body: ExplainResponse = { txn_id: txn.txn_id, shap: shapFor(txn), explanation: txn.explanation ?? "", language: "en" };
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
    const rank = { high: 3, medium: 2, low: 1 };
    const open = CASES.filter((c) => c.status !== "closed").sort(
      (a, b) => rank[b.severity] - rank[a.severity] || Date.parse(a.created_at) - Date.parse(b.created_at),
    ); // most severe first, then oldest (spec 04)
    return HttpResponse.json(page(open.map(alertOf), request));
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
      ...(body.severity && { severity: body.severity }),
    });
    return HttpResponse.json(found);
  }),

  http.post(url("/reports"), async ({ request }) => {
    const auth = needUser(request, ["customer"]);
    if (auth.error) return auth.error;
    const body = (await request.json()) as ReportCreate;
    const vpa = body.payee_vpa.trim().toLowerCase();
    const seen = reporters.get(vpa) ?? new Set<string>();
    if (seen.has(auth.user.id)) return apiError(409, "ALREADY_REPORTED", "You have already reported this payee");
    seen.add(auth.user.id);
    reporters.set(vpa, seen);
    const count = reportCount(vpa);
    const created: ReportCreated = { report_id: crypto.randomUUID(), payee_vpa: vpa, report_count: count, ...reputationOf(count) };
    return HttpResponse.json(created, { status: 201 });
  }),

  http.get(url("/payees/:vpa/reputation"), ({ request, params }) => {
    const auth = needUser(request);
    if (auth.error) return auth.error;
    const vpa = String(params.vpa);
    const scam = isScamVpa(vpa);
    const count = reportCount(vpa);
    const body: PayeeReputation = {
      vpa,
      known: true,
      ...reputationOf(count),
      report_count: count,
      confirmed_fraud_count: 0,
      graph_flag: false,
      account_age_days: scam ? 12 : 1460,
      reports_by_type: scam ? { fake_support_refund: 5, digital_arrest: 3 } : {},
      recent_reports: auth.user.role === "customer" ? null : scam
        ? [
            { report_id: "00000000-0000-4000-b000-000000000001", scam_type: "fake_support_refund", description: "Called as bank support, asked me to pay to get a refund", phone: "98xxxxxx21", created_at: DEMO_TIME },
            { report_id: "00000000-0000-4000-b000-000000000002", scam_type: "digital_arrest", description: null, phone: null, created_at: DEMO_TIME },
          ]
        : [],
    };
    return HttpResponse.json(body);
  }),

  http.get(url("/graph/entity/:id"), ({ request, params }) => {
    const auth = needUser(request, ["analyst"]);
    if (auth.error) return auth.error;
    const depth = Number(new URL(request.url).searchParams.get("depth") ?? "2");
    if (!(depth >= 1 && depth <= 3)) return apiError(400, "VALIDATION_ERROR", "depth must be 1-3");
    const found = neighbourhood(decodeURIComponent(String(params.id)), depth);
    return found ? HttpResponse.json(found) : apiError(404, "NOT_FOUND", "Unknown graph node");
  }),

  http.get(url("/graph/rings"), ({ request }) => {
    const auth = needUser(request, ["analyst"]);
    if (auth.error) return auth.error;
    return HttpResponse.json(ringPage(new URL(request.url).searchParams));
  }),

  http.get(url("/metrics/overview"), ({ request }) => {
    const auth = needUser(request, ["analyst", "admin"]);
    if (auth.error) return auth.error;
    return HttpResponse.json(metrics());
  }),

  alertsLink.addEventListener("connection", ({ client }) => {
    // While an analyst is watching: a payment every 2.5 s (seen by polling /transactions) and a
    // HOLD every 15 s pushed as alert.created, like the worker.
    const payments = setInterval(() => newPayment(false), 2_500);
    const holds = setInterval(() => client.send(JSON.stringify(alertCreated(newPayment(true)))), 15_000);
    client.addEventListener("close", () => {
      clearInterval(payments);
      clearInterval(holds);
    });
  }),

  customerLink.addEventListener("connection", () => undefined),
];
