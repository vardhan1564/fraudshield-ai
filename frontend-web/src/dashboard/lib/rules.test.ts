import { describe, expect, it } from "vitest";

import {
  emptyDraft,
  fieldOf,
  hasErrors,
  MAX_CLAUSES,
  opsFor,
  RULE_FIELDS,
  type RuleDraft,
  summarize,
  toRuleIn,
  validateDraft,
  withField,
} from "@/dashboard/lib/rules";

const draft = (change: Partial<RuleDraft>): RuleDraft => ({ ...emptyDraft(), name: "Big new payee", ...change });

describe("rule fields", () => {
  it("are the 38 model features, typed like the API checks them", () => {
    expect(RULE_FIELDS).toHaveLength(38);
    expect(fieldOf("amount")).toMatchObject({ type: "number", integer: false });
    expect(fieldOf("txn_count_1h")).toMatchObject({ type: "number", integer: true });
    expect(fieldOf("payee_is_new")?.type).toBe("bool");
    expect(fieldOf("merchant_category")?.type).toBe("category");
    expect(opsFor(fieldOf("payee_is_new"))).toEqual(["==", "!="]);
    expect(opsFor(fieldOf("amount"))).toContain(">=");
  });

  it("changing the feature keeps a valid operator and clears the value", () => {
    expect(withField({ field: "amount", op: ">=", value: "5000" }, "payee_is_new")).toEqual({ field: "payee_is_new", op: "==", value: "" });
    expect(withField({ field: "amount", op: "!=", value: "1" }, "hour")).toEqual({ field: "hour", op: "!=", value: "" });
  });
});

describe("validateDraft (spec 06 §4: invalid conditions rejected before submit)", () => {
  it("accepts a valid rule and builds the API body with typed values", () => {
    const d = draft({
      match: "all",
      clauses: [
        { field: "amount", op: ">=", value: "50000" },
        { field: "payee_is_new", op: "==", value: "true" },
        { field: "merchant_category", op: "!=", value: "FUEL" },
      ],
      action: "HOLD",
      priority: "5",
    });
    expect(hasErrors(validateDraft(d))).toBe(false);
    expect(toRuleIn(d)).toEqual({
      name: "Big new payee",
      action: "HOLD",
      priority: 5,
      is_active: true,
      condition_json: { all: [
        { field: "amount", op: ">=", value: 50000 },
        { field: "payee_is_new", op: "==", value: true },
        { field: "merchant_category", op: "!=", value: "FUEL" },
      ] },
    });
  });

  it("names exactly what is wrong in each clause", () => {
    const e = validateDraft(draft({
      clauses: [
        { field: "", op: ">", value: "1" }, // no feature
        { field: "payee_is_new", op: ">", value: "true" }, // > on yes/no
        { field: "amount", op: ">", value: "lots" }, // not a number
        { field: "txn_count_1h", op: ">", value: "2.5" }, // a count is whole
        { field: "payee_is_new", op: "==", value: "" }, // yes/no not chosen
        { field: "merchant_category", op: "==", value: "CASINO" }, // unknown category
      ],
    }));
    expect(e.clauses).toEqual(["field", "op", "number", "integer", "bool", "category"]);
  });

  it("checks the name, priority and number of clauses", () => {
    expect(validateDraft(draft({ name: "  " })).name).toBe("required");
    expect(validateDraft(draft({ name: "x".repeat(101) })).name).toBe("tooLong");
    expect(validateDraft(draft({ priority: "10001" })).priority).toBe("range");
    expect(validateDraft(draft({ priority: "-1" })).priority).toBe("range");
    expect(validateDraft(draft({ clauses: [] })).count).toBe("none");
    const many = Array.from({ length: MAX_CLAUSES + 1 }, () => ({ field: "amount", op: ">" as const, value: "1" }));
    expect(validateDraft(draft({ clauses: many })).count).toBe("tooMany");
  });
});

it("summarize writes the condition on one line", () => {
  expect(summarize({ any: [{ field: "amount", op: ">", value: 9000 }, { field: "is_night", op: "==", value: true }] }))
    .toBe("amount > 9000 OR is_night == true");
});
