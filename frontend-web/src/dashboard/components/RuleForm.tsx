import { Braces, Loader2, Plus, Trash2 } from "lucide-react";
import { type FormEvent, useId, useState } from "react";
import { useTranslation } from "react-i18next";

import { Segmented } from "@/dashboard/components/Segmented";
import {
  type ClauseDraft,
  type DraftErrors,
  EMPTY_CLAUSE,
  fieldOf,
  hasErrors,
  MAX_CLAUSES,
  MERCHANT_CATEGORIES,
  opsFor,
  RULE_FIELDS,
  type RuleDraft,
  toRuleIn,
  validateDraft,
  withField,
} from "@/dashboard/lib/rules";
import { Button } from "@/shared/components/ui/button";
import { Card } from "@/shared/components/ui/card";
import { cn } from "@/shared/lib/cn";
import type { RuleIn } from "@/shared/types/api";

const GROUPS = ["txn", "velocity", "payee", "device", "behaviour", "context", "coach", "graph"] as const;
const control =
  "h-9 rounded-lg border bg-card px-2.5 text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/20 aria-[invalid=true]:border-destructive";

function ClauseRow({ clause, index, error, onChange, onRemove, canRemove }: {
  clause: ClauseDraft;
  index: number;
  error: DraftErrors["clauses"][number];
  onChange: (clause: ClauseDraft) => void;
  onRemove: () => void;
  canRemove: boolean;
}) {
  const { t } = useTranslation();
  const field = fieldOf(clause.field);
  const errorId = useId();
  const n = index + 1;
  const invalid = (part: "field" | "op" | "value") =>
    (error === "field" && part === "field") || (error === "op" && part === "op") ||
    (error !== undefined && error !== "field" && error !== "op" && part === "value");

  return (
    <li className="rounded-lg border bg-muted/30 p-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <span className="w-6 text-center text-xs font-medium text-muted-foreground tabular-nums">{n}</span>
        <select
          aria-label={t("rules.form.feature", { n })}
          value={clause.field}
          onChange={(e) => onChange(withField(clause, e.target.value))}
          aria-invalid={invalid("field") || undefined}
          aria-describedby={error ? errorId : undefined}
          className={cn(control, "min-w-48 flex-1 font-mono text-[13px]")}
        >
          <option value="">{t("rules.form.pickFeature")}</option>
          {GROUPS.map((g) => (
            <optgroup key={g} label={t(`rules.groups.${g}`)}>
              {RULE_FIELDS.filter((f) => f.group === g).map((f) => (
                <option key={f.name} value={f.name}>{f.name}</option>
              ))}
            </optgroup>
          ))}
        </select>
        <select
          aria-label={t("rules.form.op", { n })}
          value={clause.op}
          onChange={(e) => onChange({ ...clause, op: e.target.value as ClauseDraft["op"] })}
          aria-invalid={invalid("op") || undefined}
          className={cn(control, "w-16 font-mono")}
        >
          {opsFor(field).map((op) => <option key={op} value={op}>{op}</option>)}
        </select>
        {field?.type === "bool" || field?.type === "category" ? (
          <select
            aria-label={t("rules.form.value", { n })}
            value={clause.value}
            onChange={(e) => onChange({ ...clause, value: e.target.value })}
            aria-invalid={invalid("value") || undefined}
            className={cn(control, "w-40")}
          >
            <option value="">{t("rules.form.pickValue")}</option>
            {field.type === "bool"
              ? (["true", "false"] as const).map((v) => <option key={v} value={v}>{v}</option>)
              : MERCHANT_CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        ) : (
          <input
            aria-label={t("rules.form.value", { n })}
            value={clause.value}
            onChange={(e) => onChange({ ...clause, value: e.target.value })}
            inputMode="decimal"
            placeholder={field?.integer ? "0" : "0.0"}
            aria-invalid={invalid("value") || undefined}
            className={cn(control, "w-40 font-mono tabular-nums")}
          />
        )}
        <Button
          variant="ghost"
          size="icon"
          className="size-9"
          onClick={onRemove}
          disabled={!canRemove}
          aria-label={t("rules.form.removeClause", { n })}
          title={t("rules.form.removeClause", { n })}
        >
          <Trash2 aria-hidden="true" />
        </Button>
      </div>
      {error && <p id={errorId} className="mt-1.5 pl-8 text-[13px] text-destructive">{t(`rules.errors.${error}`)}</p>}
    </li>
  );
}

/**
 * Spec 06 §3 Rules Manager form builder: name, all/any, 1-20 clauses (feature → only the
 * operators its type allows → a value of that type), action, priority, active, and the JSON the
 * API will get. Every check runs before submit (spec 06 §4); the API's own message shows if it
 * still says no (e.g. a taken name).
 */
export function RuleForm({ initial, title, saving, serverError, onSave, onCancel }: {
  initial: RuleDraft;
  title: string;
  saving: boolean;
  serverError: string | null;
  onSave: (body: RuleIn) => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState(initial);
  const [tried, setTried] = useState(false);
  const errors = validateDraft(draft);
  const shown: DraftErrors = tried ? errors : { clauses: [] };
  const set = (change: Partial<RuleDraft>) => setDraft((d) => ({ ...d, ...change }));
  const setClause = (i: number, clause: ClauseDraft) =>
    setDraft((d) => ({ ...d, clauses: d.clauses.map((c, j) => (j === i ? clause : c)) }));

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    setTried(true);
    if (!hasErrors(errors)) onSave(toRuleIn(draft));
  };

  const preview = JSON.stringify(toRuleIn(draft).condition_json, null, 2);
  const blocked = tried && hasErrors(errors);

  return (
    <Card className="p-5">
      <form onSubmit={onSubmit} noValidate aria-label={title}>
        <h2 className="text-[15px] font-semibold">{title}</h2>
        <div className="mt-4 grid gap-5 xl:grid-cols-[minmax(0,1fr)_320px]">
          <div className="min-w-0 space-y-4">
            <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto_auto]">
              <div>
                <label htmlFor="rule-name" className="text-xs font-medium text-muted-foreground">{t("rules.form.name")}</label>
                <input
                  id="rule-name"
                  value={draft.name}
                  onChange={(e) => set({ name: e.target.value })}
                  maxLength={120}
                  aria-invalid={Boolean(shown.name) || undefined}
                  className={cn(control, "mt-1 w-full")}
                  placeholder={t("rules.form.namePlaceholder")}
                />
                {shown.name && <p className="mt-1 text-[13px] text-destructive">{t(`rules.errors.name_${shown.name}`)}</p>}
              </div>
              <div>
                <label htmlFor="rule-priority" className="text-xs font-medium text-muted-foreground">{t("rules.form.priority")}</label>
                <input
                  id="rule-priority"
                  value={draft.priority}
                  onChange={(e) => set({ priority: e.target.value })}
                  inputMode="numeric"
                  aria-invalid={Boolean(shown.priority) || undefined}
                  className={cn(control, "mt-1 w-28 tabular-nums")}
                />
                {shown.priority && <p className="mt-1 text-[13px] text-destructive">{t("rules.errors.priority")}</p>}
              </div>
              <label className="flex items-center gap-2 self-end pb-2 text-sm">
                <input type="checkbox" checked={draft.isActive} onChange={(e) => set({ isActive: e.target.checked })} className="size-4 accent-[var(--primary)]" />
                {t("rules.form.active")}
              </label>
            </div>

            <div className="flex flex-wrap items-center gap-3">
              <Segmented<"all" | "any">
                label={t("rules.form.match")}
                value={draft.match}
                onChange={(match) => set({ match })}
                options={[{ value: "all", label: t("rules.form.matchAll") }, { value: "any", label: t("rules.form.matchAny") }]}
              />
              <span className="text-sm text-muted-foreground">{t("rules.form.then")}</span>
              <Segmented<RuleIn["action"]>
                label={t("rules.form.action")}
                value={draft.action}
                onChange={(action) => set({ action })}
                options={[{ value: "STEP_UP", label: t("decisions.STEP_UP") }, { value: "HOLD", label: t("decisions.HOLD") }]}
              />
            </div>

            <ol className="space-y-2" aria-label={t("rules.form.conditions")}>
              {draft.clauses.map((c, i) => (
                <ClauseRow
                  key={i}
                  clause={c}
                  index={i}
                  error={shown.clauses[i]}
                  onChange={(clause) => setClause(i, clause)}
                  onRemove={() => set({ clauses: draft.clauses.filter((_, j) => j !== i) })}
                  canRemove={draft.clauses.length > 1}
                />
              ))}
            </ol>
            <Button
              variant="outline"
              size="sm"
              onClick={() => set({ clauses: [...draft.clauses, { ...EMPTY_CLAUSE }] })}
              disabled={draft.clauses.length >= MAX_CLAUSES}
            >
              <Plus aria-hidden="true" />
              {t("rules.form.addClause")}
            </Button>
            {shown.count && <p className="text-[13px] text-destructive">{t(`rules.errors.count_${shown.count}`)}</p>}
          </div>

          <div className="min-w-0">
            <p className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
              <Braces className="size-3.5" aria-hidden="true" />
              {t("rules.form.preview")}
            </p>
            <pre
              aria-label={t("rules.form.preview")}
              className="mt-1 max-h-80 overflow-auto rounded-lg border bg-muted/50 p-3 font-mono text-[12px] leading-relaxed"
            >
              {preview}
            </pre>
          </div>
        </div>

        {blocked && (
          <p role="alert" className="mt-4 rounded-lg border border-destructive/40 bg-destructive/10 px-3.5 py-2.5 text-sm">
            {t("rules.errors.fix")}
          </p>
        )}
        {serverError && (
          <p role="alert" className="mt-4 rounded-lg border border-destructive/40 bg-destructive/10 px-3.5 py-2.5 text-sm">
            {serverError}
          </p>
        )}
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="ghost" onClick={onCancel} disabled={saving}>{t("rules.form.cancel")}</Button>
          <Button type="submit" disabled={saving}>
            {saving && <Loader2 className="animate-spin" aria-hidden="true" />}
            {saving ? t("rules.form.saving") : t("rules.form.save")}
          </Button>
        </div>
      </form>
    </Card>
  );
}
