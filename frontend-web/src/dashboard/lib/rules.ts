import featuresFile from "../../../../ml/features/features.json";
import type { MerchantCategory, Rule, RuleClause, RuleCondition, RuleIn, RuleOp } from "@/shared/types/api";

/**
 * The rule form's model and checks. The feature list is ml/features/features.json, the same file
 * the backend validates against (spec 03 §2), and the checks mirror backend/app/schemas/rules.py
 * `check_condition`, so the form rejects what the API would reject before anything is sent.
 */

export type FieldType = "number" | "bool" | "category";

export interface RuleField {
  name: string;
  group: string;
  type: FieldType;
  integer: boolean;
}

export const RULE_FIELDS: readonly RuleField[] = featuresFile.features.map((f) => ({
  name: f.name,
  group: f.group,
  type: f.dtype === "bool" ? "bool" : f.dtype === "category" ? "category" : "number",
  integer: f.dtype === "int64",
}));

const BY_NAME = new Map(RULE_FIELDS.map((f) => [f.name, f]));

export const MERCHANT_CATEGORIES: readonly MerchantCategory[] = [
  "P2P", "GROCERY", "FOOD", "FUEL", "UTILITIES", "ECOMMERCE", "TRAVEL", "ENTERTAINMENT", "HEALTH", "EDUCATION", "RENT",
];
export const ALL_OPS: readonly RuleOp[] = [">", ">=", "<", "<=", "==", "!="];
export const MAX_CLAUSES = 20;
export const NAME_MAX = 100;
export const PRIORITY_MAX = 10_000;

export function fieldOf(name: string): RuleField | undefined {
  return BY_NAME.get(name);
}

/** Yes/no and category features only compare for equality (as the API). */
export function opsFor(field: RuleField | undefined): readonly RuleOp[] {
  return field && field.type !== "number" ? ["==", "!="] : ALL_OPS;
}

/** What the form edits: values stay text until checked, so a half-typed number is fine. */
export interface ClauseDraft {
  field: string;
  op: RuleOp;
  value: string;
}

export interface RuleDraft {
  name: string;
  match: "all" | "any";
  clauses: ClauseDraft[];
  action: RuleIn["action"];
  priority: string;
  isActive: boolean;
}

export const EMPTY_CLAUSE: ClauseDraft = { field: "", op: ">", value: "" };

export function emptyDraft(): RuleDraft {
  return { name: "", match: "all", clauses: [{ ...EMPTY_CLAUSE }], action: "STEP_UP", priority: "100", isActive: true };
}

export function draftOf(rule: Rule): RuleDraft {
  const match = "all" in rule.condition_json ? "all" : "any";
  const clauses = "all" in rule.condition_json ? rule.condition_json.all : rule.condition_json.any;
  return {
    name: rule.name,
    match,
    clauses: clauses.map((c) => ({ field: c.field, op: c.op, value: String(c.value) })),
    action: rule.action,
    priority: String(rule.priority),
    isActive: rule.is_active,
  };
}

/** Changing the feature keeps the operator only if the new type allows it, and clears the value. */
export function withField(clause: ClauseDraft, name: string): ClauseDraft {
  const ops = opsFor(fieldOf(name));
  return { field: name, op: ops.includes(clause.op) ? clause.op : (ops[0] ?? "=="), value: "" };
}

function clauseValue(field: RuleField, raw: string): RuleClause["value"] | undefined {
  if (field.type === "bool") return raw === "true" ? true : raw === "false" ? false : undefined;
  if (field.type === "category") return (MERCHANT_CATEGORIES as readonly string[]).includes(raw) ? raw : undefined;
  const text = raw.trim();
  if (!/^-?\d+(\.\d+)?$/.test(text)) return undefined;
  const n = Number(text);
  if (field.integer && !Number.isInteger(n)) return undefined;
  return n;
}

export type ClauseError = "field" | "op" | "number" | "integer" | "bool" | "category";

export interface DraftErrors {
  name?: "required" | "tooLong";
  priority?: "range";
  clauses: (ClauseError | undefined)[];
  count?: "none" | "tooMany";
}

export function hasErrors(e: DraftErrors): boolean {
  return Boolean(e.name || e.priority || e.count || e.clauses.some(Boolean));
}

export function validateDraft(draft: RuleDraft): DraftErrors {
  const errors: DraftErrors = { clauses: [] };
  const name = draft.name.trim();
  if (!name) errors.name = "required";
  else if (name.length > NAME_MAX) errors.name = "tooLong";
  const priority = Number(draft.priority);
  if (!/^\d+$/.test(draft.priority.trim()) || priority > PRIORITY_MAX) errors.priority = "range";
  if (draft.clauses.length === 0) errors.count = "none";
  if (draft.clauses.length > MAX_CLAUSES) errors.count = "tooMany";
  errors.clauses = draft.clauses.map((c) => {
    const field = fieldOf(c.field);
    if (!field) return "field";
    if (!opsFor(field).includes(c.op)) return "op";
    if (clauseValue(field, c.value) !== undefined) return undefined;
    if (field.type === "number") return field.integer && /^-?\d+\.\d+$/.test(c.value.trim()) ? "integer" : "number";
    return field.type;
  });
  return errors;
}

/** The API body (call only when `validateDraft` found nothing). */
export function toRuleIn(draft: RuleDraft): RuleIn {
  const clauses: RuleClause[] = draft.clauses.map((c) => {
    const field = fieldOf(c.field);
    const value = field ? clauseValue(field, c.value) : undefined;
    return { field: c.field, op: c.op, value: value ?? c.value };
  });
  const condition_json: RuleCondition = draft.match === "all" ? { all: clauses } : { any: clauses };
  return {
    name: draft.name.trim(),
    condition_json,
    action: draft.action,
    priority: Number(draft.priority),
    is_active: draft.isActive,
  };
}

/** One line for the table: `amount >= 50000 AND payee_is_new == true`. */
export function summarize(condition: RuleCondition): string {
  const join = "all" in condition ? " AND " : " OR ";
  const clauses = "all" in condition ? condition.all : condition.any;
  return clauses.map((c) => `${c.field} ${c.op} ${String(c.value)}`).join(join);
}
