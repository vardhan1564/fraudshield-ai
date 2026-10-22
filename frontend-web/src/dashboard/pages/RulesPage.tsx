import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus, SlidersHorizontal, Trash2 } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { RuleForm } from "@/dashboard/components/RuleForm";
import { draftOf, emptyDraft, summarize } from "@/dashboard/lib/rules";
import { toApiError } from "@/shared/api/client";
import { createRule, deleteRule, listRules, updateRule } from "@/shared/api/endpoints/rules";
import { DecisionBadge } from "@/shared/components/DecisionBadge";
import { EmptyState } from "@/shared/components/EmptyState";
import { ErrorState } from "@/shared/components/ErrorState";
import { Button } from "@/shared/components/ui/button";
import { Card } from "@/shared/components/ui/card";
import { useToast } from "@/shared/hooks/useToast";
import { cn } from "@/shared/lib/cn";
import { formatDateTime } from "@/shared/lib/format";
import type { Rule, RuleIn } from "@/shared/types/api";

const RULES_KEY = ["rules"] as const;

type Editing = { mode: "new" } | { mode: "edit"; rule: Rule } | null;

function ActiveSwitch({ rule, busy, onToggle }: { rule: Rule; busy: boolean; onToggle: () => void }) {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      role="switch"
      aria-checked={rule.is_active}
      aria-label={t("rules.activeFor", { name: rule.name })}
      disabled={busy}
      onClick={onToggle}
      className="inline-flex cursor-pointer items-center gap-2 text-[13px] disabled:opacity-50"
    >
      <span className={cn("relative inline-flex h-5 w-9 items-center rounded-full transition-colors", rule.is_active ? "bg-primary" : "bg-muted-foreground/35")}>
        <span className={cn("size-4 rounded-full bg-white shadow transition-transform", rule.is_active ? "translate-x-[18px]" : "translate-x-0.5")} />
      </span>
      {rule.is_active ? t("rules.on") : t("rules.off")}
    </button>
  );
}

/**
 * Spec 06 §3 Rules Manager (admin): the rules that can only raise a decision (spec 04 §1.1),
 * lower priority first. New / edit open the form builder; a change reaches every API process on
 * its next payment.
 */
export function RulesPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [editing, setEditing] = useState<Editing>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  const rules = useQuery({ queryKey: RULES_KEY, queryFn: () => listRules() });
  const refresh = () => queryClient.invalidateQueries({ queryKey: RULES_KEY });

  const save = useMutation({
    mutationFn: ({ body, id }: { body: RuleIn; id?: string }) => (id ? updateRule(id, body) : createRule(body)),
    onSuccess: (rule, { id }) => {
      setEditing(null);
      toast({ tone: "success", title: t(id ? "rules.toast.updated" : "rules.toast.created"), body: rule.name });
      void refresh();
    },
  });
  const toggle = useMutation({
    mutationFn: (rule: Rule) =>
      updateRule(rule.id, { name: rule.name, condition_json: rule.condition_json, action: rule.action, priority: rule.priority, is_active: !rule.is_active }),
    onSuccess: (rule) => {
      toast({ tone: "success", title: t(rule.is_active ? "rules.toast.on" : "rules.toast.off"), body: rule.name });
      void refresh();
    },
    onError: (error) => toast({ tone: "error", title: t("rules.toast.failed"), body: toApiError(error).message }),
  });
  const remove = useMutation({
    mutationFn: (rule: Rule) => deleteRule(rule.id),
    onSuccess: (_void, rule) => {
      setConfirmDelete(null);
      toast({ tone: "success", title: t("rules.toast.deleted"), body: rule.name });
      void refresh();
    },
    onError: (error) => toast({ tone: "error", title: t("rules.toast.failed"), body: toApiError(error).message }),
  });

  const open = (next: Editing) => {
    save.reset();
    setEditing(next);
    window.scrollTo?.({ top: 0, behavior: "smooth" });
  };
  const sorted = [...(rules.data?.items ?? [])].sort((a, b) => a.priority - b.priority || a.name.localeCompare(b.name));
  const head = "px-3 py-2.5 text-left text-xs font-medium text-muted-foreground";

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="max-w-2xl text-sm text-muted-foreground">{t("rules.intro")}</p>
        {!editing && (
          <Button onClick={() => open({ mode: "new" })}>
            <Plus aria-hidden="true" />
            {t("rules.new")}
          </Button>
        )}
      </div>

      {editing && (
        <RuleForm
          key={editing.mode === "edit" ? editing.rule.id : "new"}
          title={editing.mode === "edit" ? t("rules.editTitle", { name: editing.rule.name }) : t("rules.newTitle")}
          initial={editing.mode === "edit" ? draftOf(editing.rule) : emptyDraft()}
          saving={save.isPending}
          serverError={save.isError ? toApiError(save.error).message : null}
          onSave={(body) => save.mutate({ body, id: editing.mode === "edit" ? editing.rule.id : undefined })}
          onCancel={() => setEditing(null)}
        />
      )}

      {rules.isError ? (
        <ErrorState title={t("rules.error")} message={toApiError(rules.error).message} onRetry={() => void rules.refetch()} className="min-h-72" />
      ) : !rules.data ? (
        <Card><div className="space-y-2 p-4" aria-busy="true">{Array.from({ length: 4 }, (_, i) => <div key={i} className="h-11 animate-pulse rounded-md bg-muted/70" />)}</div></Card>
      ) : sorted.length === 0 ? (
        <EmptyState icon={SlidersHorizontal} title={t("rules.empty")} body={t("rules.emptyBody")} className="min-h-60" />
      ) : (
        <Card className="overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[900px] text-sm">
              <thead>
                <tr className="border-b">
                  <th scope="col" className={cn(head, "w-20")}>{t("rules.col.priority")}</th>
                  <th scope="col" className={head}>{t("rules.col.name")}</th>
                  <th scope="col" className={head}>{t("rules.col.condition")}</th>
                  <th scope="col" className={head}>{t("rules.col.action")}</th>
                  <th scope="col" className={head}>{t("rules.col.active")}</th>
                  <th scope="col" className={head}>{t("rules.col.created")}</th>
                  <th scope="col" className={head}><span className="sr-only">{t("rules.col.actions")}</span></th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((r) => (
                  <tr key={r.id} className={cn("border-t align-top", !r.is_active && "text-muted-foreground")}>
                    <td className="px-3 py-3 tabular-nums">{r.priority}</td>
                    <td className="px-3 py-3 font-medium">{r.name}</td>
                    <td className="max-w-md px-3 py-3 font-mono text-[12.5px] leading-relaxed">{summarize(r.condition_json)}</td>
                    <td className="whitespace-nowrap px-3 py-3"><DecisionBadge decision={r.action} /></td>
                    <td className="px-3 py-3">
                      <ActiveSwitch rule={r} busy={toggle.isPending && toggle.variables.id === r.id} onToggle={() => toggle.mutate(r)} />
                    </td>
                    <td className="whitespace-nowrap px-3 py-3 text-muted-foreground">{formatDateTime(r.created_at)}</td>
                    <td className="whitespace-nowrap px-3 py-2 text-right">
                      {confirmDelete === r.id ? (
                        <span className="inline-flex items-center gap-2">
                          <span className="text-[13px]">{t("rules.confirmDelete")}</span>
                          <Button size="sm" variant="destructive" disabled={remove.isPending} onClick={() => remove.mutate(r)}>{t("rules.delete")}</Button>
                          <Button size="sm" variant="ghost" onClick={() => setConfirmDelete(null)}>{t("rules.form.cancel")}</Button>
                        </span>
                      ) : (
                        <span className="inline-flex gap-1">
                          <Button size="sm" variant="ghost" onClick={() => open({ mode: "edit", rule: r })} aria-label={t("rules.editFor", { name: r.name })}>
                            <Pencil aria-hidden="true" />
                            {t("rules.edit")}
                          </Button>
                          <Button size="sm" variant="ghost" onClick={() => setConfirmDelete(r.id)} aria-label={t("rules.deleteFor", { name: r.name })}>
                            <Trash2 aria-hidden="true" />
                          </Button>
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
