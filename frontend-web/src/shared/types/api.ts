/**
 * Types for the FraudShield API. They mirror docs/specs/04-api-spec.md and the backend's
 * Pydantic schemas exactly (field names included): change both together.
 * UUIDs and timestamps (ISO-8601 with offset) are strings on the wire.
 */

// --- shared ----------------------------------------------------------------------------------

export type Uuid = string;
export type IsoDateTime = string;

export type Role = "customer" | "analyst" | "admin" | "partner";
export type Language = "en" | "hi" | "mr";
export type Channel = "UPI" | "CARD" | "WALLET";
export type Decision = "APPROVE" | "STEP_UP" | "HOLD";
export type Action = "NONE" | "SCAM_COACH" | "OTP" | "TRUSTED_CONTACT" | "ANALYST_REVIEW";
export type MerchantCategory =
  | "P2P" | "GROCERY" | "FOOD" | "FUEL" | "UTILITIES" | "ECOMMERCE"
  | "TRAVEL" | "ENTERTAINMENT" | "HEALTH" | "EDUCATION" | "RENT";

/** Every error response: `{"error": {"code", "message", "details"}}` (spec 04). */
export interface ApiErrorBody {
  error: { code: string; message: string; details?: Record<string, unknown> };
}

/** Lists: `?page=1&page_size=50` (page_size ≤ 200). */
export interface Page<T> {
  items: T[];
  page: number;
  page_size: number;
  total: number;
}

// --- auth (endpoints 1-4) --------------------------------------------------------------------

export interface LoginRequest {
  email: string;
  password: string;
}

export interface RegisterRequest {
  name: string;
  phone: string; // 10-digit Indian mobile
  email: string;
  password: string; // 8-72 bytes
  language?: Language;
}

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: "bearer";
  expires_in: number; // seconds
}

export interface Me {
  id: Uuid;
  name: string;
  phone: string; // masked: 98xxxxxx21
  email: string | null;
  role: Role;
  language: Language;
}

// --- scoring (endpoint 5) and explanations (9) -------------------------------------------------

export interface Geo {
  lat: number;
  lon: number;
}

export interface PaymentContext {
  on_call?: boolean;
  caller_in_contacts?: boolean;
  call_duration_s?: number;
  screen_share_active?: boolean;
  remote_access_app?: string | null;
  is_vpn?: boolean;
}

export interface Behaviour {
  typing_speed_z?: number;
  session_len_s?: number;
  login_to_pay_s?: number;
}

export interface ScoreRequest {
  txn_id: string;
  user_id: string;
  amount: number; // > 0, ≤ 10,000,000
  currency?: "INR";
  channel: Channel;
  payee_vpa: string;
  merchant_category?: MerchantCategory;
  device_id?: string | null;
  ip?: string | null;
  geo?: Geo | null;
  timestamp: IsoDateTime;
  context?: PaymentContext;
  behaviour?: Behaviour;
}

export interface ModelScores {
  transaction: number;
  anomaly: number;
  graph: number;
  social_eng: number;
}

export interface Reason {
  feature: string;
  value: number | boolean | string;
  impact: number; // meta-model log-odds; rule entries carry the rule's boost
  text: string; // plain language, in the user's language
}

export interface CoachQuestion {
  key: string;
  text: string;
}

export interface ScoreResponse {
  txn_id: string;
  risk_score: number;
  decision: Decision;
  action: Action;
  model_scores: ModelScores;
  top_reasons: Reason[];
  explanation: string;
  explanation_status: "ready" | "pending";
  coach_questions: CoachQuestion[];
  model_version: string;
  degraded: string[];
  latency_ms: number;
}

export interface ExplainResponse {
  txn_id: string;
  shap: Reason[]; // top 10 by |impact|, both directions
  explanation: string;
  language: Language;
}

// --- transactions (endpoints 7-8) ------------------------------------------------------------

export interface TxnItem {
  id: Uuid;
  txn_id: string;
  user_id: Uuid;
  customer_external_id: string | null;
  amount: number;
  currency: string;
  channel: Channel;
  merchant_category: string | null;
  payee_vpa: string;
  risk_score: number | null;
  decision: Decision | null;
  action: string | null;
  model_version: string | null;
  latency_ms: number | null;
  ts: IsoDateTime;
  case_id: Uuid | null; // the case, if the payment was held (T5.3)
}

export interface TxnDetail extends TxnItem {
  model_scores: (ModelScores & { meta: number | null }) | null;
  top_reasons: Reason[];
  explanation: string | null;
  context: Record<string, unknown> | null;
  device: { fingerprint: string; is_rooted: boolean; first_seen: IsoDateTime } | null;
  ip: string | null;
  geo_lat: number | null;
  geo_lon: number | null;
}

export interface TxnFilters {
  mine?: boolean; // only the caller's payments; customers always get only their own (T4.5)
  decision?: Decision;
  channel?: Channel;
  user_id?: string;
  payee_vpa?: string;
  from?: IsoDateTime;
  to?: IsoDateTime;
  min_risk?: number;
  page?: number;
  page_size?: number;
}

// --- alerts and cases (endpoints 11, 13, 14) ---------------------------------------------------

export type Severity = "low" | "medium" | "high";
export type AlertStatus = "open" | "assigned" | "closed";
export type CaseLabel = "fraud" | "legit";

export interface AlertItem {
  alert_id: Uuid;
  case_id: Uuid | null;
  txn_id: string;
  severity: Severity;
  status: AlertStatus;
  assigned_to: Uuid | null;
  risk_score: number | null;
  decision: Decision | null;
  amount: number;
  channel: Channel;
  top_reasons: Reason[];
  created_at: IsoDateTime;
}

export interface CaseDetail {
  case_id: Uuid;
  alert_id: Uuid;
  severity: Severity;
  status: AlertStatus;
  assigned_to: Uuid | null;
  label: CaseLabel | null;
  notes: string | null;
  resolved_by: Uuid | null;
  resolved_at: IsoDateTime | null;
  created_at: IsoDateTime;
  transaction: {
    id: Uuid;
    txn_id: string;
    amount: number;
    currency: string;
    channel: Channel;
    merchant_category: string | null;
    payee_vpa: string;
    risk_score: number | null;
    decision: Decision | null;
    action: string | null;
    model_version: string | null;
    ts: IsoDateTime;
    customer: { id: Uuid; external_id: string | null; name: string; phone: string | null };
  };
  top_reasons: Reason[];
  explanation: string | null;
}

/** `null` for status/label/notes/severity = no change; `assigned_to: null` unassigns. */
export interface CaseUpdate {
  status?: AlertStatus;
  label?: CaseLabel;
  notes?: string;
  assigned_to?: Uuid | null;
  severity?: Severity; // the alert's; Escalate raises it to high (T4.4)
}

// --- reports and payee reputation (endpoints 17-18) ------------------------------------------

export type ScamType =
  | "digital_arrest" | "fake_support_refund" | "investment_task" | "fake_kyc"
  | "lottery_prize" | "job_task" | "phishing" | "other";
export type RiskBand = "low" | "medium" | "high";

export interface ReportCreate {
  payee_vpa: string;
  phone?: string | null;
  scam_type: ScamType;
  description?: string | null;
  evidence_url?: string | null;
}

export interface ReportCreated {
  report_id: Uuid;
  payee_vpa: string;
  report_count: number;
  reputation_score: number;
  risk_level: RiskBand;
}

export interface PayeeReputation {
  vpa: string;
  known: boolean;
  reputation_score: number;
  risk_level: RiskBand;
  report_count: number;
  confirmed_fraud_count: number;
  graph_flag: boolean;
  account_age_days: number | null;
  reports_by_type: Record<string, number>;
  recent_reports:
    | { report_id: Uuid; scam_type: string; description: string | null; phone: string | null;
        created_at: IsoDateTime }[]
    | null; // analysts/admins only
}

// --- graph explorer (endpoints 15-16, §2.5) -------------------------------------------------

/** `A:{user}`, `P:{vpa}`, `D:{fingerprint}` or `I:{ip}` (spec 05 §3). */
export type GraphNodeType = "Account" | "Payee" | "Device" | "IP";
export type GraphEdgeType = "SENT" | "USED_DEVICE" | "LOGGED_FROM";
export type RingStatus = "suspected" | "confirmed" | "dismissed";

export interface GraphNode {
  id: string;
  type: GraphNodeType;
  label: string; // masked: phone, VPA, IP
  risk: number | null; // accounts: graph_score; payees: reputation; devices/IPs: null
  flags: string[]; // fan_in, pass_through, shared_device, new_account_burst, gnn, mule_suspect
}

export interface GraphEdge {
  source: string;
  target: string;
  type: GraphEdgeType;
  amount: number | null;
  ts: IsoDateTime;
}

export interface RingRef {
  id: Uuid;
  members: string[];
  score: number;
}

export interface Neighbourhood {
  nodes: GraphNode[];
  edges: GraphEdge[];
  rings: RingRef[];
  truncated: boolean; // node or edge cap reached
}

export interface RingItem extends RingRef {
  size: number;
  detector: string;
  status: RingStatus;
  first_seen: IsoDateTime;
  last_seen: IsoDateTime;
}

export interface RingFilters {
  status?: RingStatus;
  detector?: string;
  min_score?: number;
  page?: number;
  page_size?: number;
}

// --- admin: rules, models, audit (endpoints 23-25, 27) ----------------------------------------

export type RuleOp = ">" | ">=" | "<" | "<=" | "==" | "!=";
export interface RuleClause {
  field: string;
  op: RuleOp;
  value: number | boolean | string;
}
export type RuleCondition = { all: RuleClause[] } | { any: RuleClause[] };

export interface RuleIn {
  name: string;
  condition_json: RuleCondition;
  action: "STEP_UP" | "HOLD";
  priority?: number; // 0-10000, lower runs first
  is_active?: boolean;
}

export interface Rule extends Required<RuleIn> {
  id: Uuid;
  updated_by: Uuid | null;
  created_at: IsoDateTime;
}

export interface ModelVersion {
  id: Uuid;
  name: string;
  version: string;
  mlflow_run_id: string | null;
  metrics_json: Record<string, unknown> | null;
  is_active: boolean;
  loaded: boolean;
  deployed_at: IsoDateTime | null;
  created_at: IsoDateTime;
}

export interface AuditEntry {
  id: number;
  ts: IsoDateTime;
  actor_id: Uuid | null;
  action: string;
  entity: string;
  entity_id: string | null;
  ip: string | null;
  before_json: Record<string, unknown> | null;
  after_json: Record<string, unknown> | null;
}

// --- metrics (endpoint 26) -------------------------------------------------------------------

export interface MetricsOverview {
  date_from: IsoDateTime;
  date_to: IsoDateTime;
  txn_count: number;
  fraud_caught: number;
  amount_saved_inr: number;
  false_positive_rate: number | null;
  p95_latency_ms: number | null;
  by_hour: { hour: number; total: number; flagged: number }[];
  by_type: { scenario: string; count: number }[];
  by_city: { city: string; flagged: number }[];
}

// --- WebSockets (spec 04 §3) -----------------------------------------------------------------

export interface AlertCreatedMessage {
  type: "alert.created";
  data: {
    alert_id: Uuid;
    case_id: Uuid;
    txn_id: string;
    risk_score: number;
    decision: Decision;
    top_reasons: Reason[];
    created_at: IsoDateTime;
  };
}
export interface ExplanationReadyMessage {
  type: "explanation.ready";
  data: { txn_id: string; explanation: string };
}
export interface CaseUpdatedMessage {
  type: "case.updated";
  data: { case_id: Uuid; status: AlertStatus; label: CaseLabel | null };
}
export interface ApprovalRequestedMessage {
  type: "approval.requested";
  data: {
    approval_id: Uuid;
    txn_id: string;
    requester_name: string;
    amount: number;
    payee_vpa: string;
    top_reasons: Reason[];
    expires_at: IsoDateTime;
  };
}
export interface ApprovalResolvedMessage {
  type: "approval.resolved";
  data: { approval_id: Uuid; txn_id: string; status: "approved" | "rejected" | "expired" };
}

export type AnalystMessage = AlertCreatedMessage | ExplanationReadyMessage | CaseUpdatedMessage;
export type CustomerMessage =
  | ExplanationReadyMessage
  | ApprovalRequestedMessage
  | ApprovalResolvedMessage;
