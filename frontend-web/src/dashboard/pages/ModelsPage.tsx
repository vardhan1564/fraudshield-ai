import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Brain, CircleCheck, CircleDashed, Rocket } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { toApiError } from "@/shared/api/client";
import { deployModel, listModels } from "@/shared/api/endpoints/models";
import { EmptyState } from "@/shared/components/EmptyState";
import { ErrorState } from "@/shared/components/ErrorState";
import { Badge } from "@/shared/components/ui/badge";
import { Button } from "@/shared/components/ui/button";
import { Card } from "@/shared/components/ui/card";
import { useToast } from "@/shared/hooks/useToast";
import { formatDateTime, formatPercent } from "@/shared/lib/format";
import type { ModelVersion } from "@/shared/types/api";

const MODELS_KEY = ["models"] as const;

/** A number at `path` in metrics_json, if the training run recorded it. */
function metric(m: ModelVersion, path: string): number | null {
  let node: unknown = m.metrics_json;
  for (const key of path.split(".")) {
    if (node === null || typeof node !== "object") return null;
    node = (node as Record<string, unknown>)[key];
  }
  return typeof node === "number" && Number.isFinite(node) ? node : null;
}

/** The figures an admin decides on (spec 03 §6): quality on the test period and the live thresholds. */
type MetricKey = "prAuc" | "rocAuc" | "recall1" | "holdRecall" | "holdFpr" | "holdAt" | "stepUpAt";
const KEY_METRICS: { key: MetricKey; path: string; kind: "ratio" | "percent" | "threshold" }[] = [
  { key: "prAuc", path: "txn_model.test.pr_auc", kind: "ratio" },
  { key: "rocAuc", path: "txn_model.test.roc_auc", kind: "ratio" },
  { key: "recall1", path: "txn_model.test.recall_at_1pct_fpr", kind: "percent" },
  { key: "holdRecall", path: "validation_out_of_fold.recall_hold", kind: "percent" },
  { key: "holdFpr", path: "validation_out_of_fold.fpr_hold", kind: "percent" },
  { key: "holdAt", path: "hold", kind: "threshold" },
  { key: "stepUpAt", path: "step_up", kind: "threshold" },
];

function show(value: number, kind: "ratio" | "percent" | "threshold"): string {
  return kind === "percent" ? formatPercent(value, 2) : value.toFixed(3);
}

/** Drift is measured daily from T5.8 (Evidently); until then the honest answer is "not checked". */
function DriftStatus({ model }: { model: ModelVersion }) {
  const { t } = useTranslation();
  const drift = model.metrics_json?.["drift"];
  const status = drift && typeof drift === "object" ? (drift as { status?: string; checked_at?: string }) : null;
  if (!status?.status) {
    return (
      <p className="flex items-center gap-2 text-sm text-muted-foreground">
        <CircleDashed className="size-4" aria-hidden="true" />
        {t("models.driftNotChecked")}
      </p>
    );
  }
  return (
    <p className="flex items-center gap-2 text-sm">
      <CircleCheck className="size-4 text-risk-low" aria-hidden="true" />
      {t("models.driftStatus", { status: status.status, time: status.checked_at ? formatDateTime(status.checked_at) : "—" })}
    </p>
  );
}

function ModelCard({ model, deploying, onDeploy }: { model: ModelVersion; deploying: boolean; onDeploy: () => void }) {
  const { t } = useTranslation();
  const [confirm, setConfirm] = useState(false);
  const figures = KEY_METRICS.map((m) => ({ ...m, value: metric(model, m.path) })).filter((m) => m.value !== null);
  return (
    <Card className="p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="font-mono text-[15px] font-semibold">{model.version}</h2>
          <p className="mt-0.5 text-[13px] text-muted-foreground">
            {model.name}
            {model.mlflow_run_id && <> · {t("models.run")} <span className="font-mono">{model.mlflow_run_id.slice(0, 12)}</span></>}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {model.is_active && <Badge>{t("models.active")}</Badge>}
          <Badge variant={model.loaded ? "outline" : "muted"}>{model.loaded ? t("models.loaded") : t("models.notLoaded")}</Badge>
        </div>
      </div>

      {figures.length > 0 && (
        <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-3 border-t pt-4 sm:grid-cols-4 xl:grid-cols-7">
          {figures.map((f) => (
            <div key={f.key}>
              <dt className="text-xs text-muted-foreground">{t(`models.metric.${f.key}`)}</dt>
              <dd className="mt-0.5 text-lg font-semibold tabular-nums">{show(f.value ?? 0, f.kind)}</dd>
            </div>
          ))}
        </dl>
      )}

      <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t pt-4">
        <div className="space-y-1">
          <DriftStatus model={model} />
          <p className="text-[13px] text-muted-foreground">
            {model.deployed_at ? t("models.deployedAt", { time: formatDateTime(model.deployed_at) }) : t("models.neverDeployed")}
          </p>
        </div>
        {model.is_active ? (
          <p className="text-[13px] text-muted-foreground">{t("models.serving")}</p>
        ) : confirm ? (
          <span className="inline-flex items-center gap-2">
            <span className="text-[13px]">{t("models.confirmDeploy")}</span>
            <Button size="sm" disabled={deploying} onClick={onDeploy}>{t("models.deploy")}</Button>
            <Button size="sm" variant="ghost" onClick={() => setConfirm(false)}>{t("rules.form.cancel")}</Button>
          </span>
        ) : (
          <Button size="sm" variant="outline" onClick={() => setConfirm(true)}>
            <Rocket aria-hidden="true" />
            {t("models.deploy")}
          </Button>
        )}
      </div>

      {model.metrics_json && (
        <details className="mt-3">
          <summary className="cursor-pointer text-[13px] font-medium text-primary">{t("models.allMetrics")}</summary>
          <pre className="mt-2 max-h-72 overflow-auto rounded-lg border bg-muted/50 p-3 font-mono text-[12px]">
            {JSON.stringify(model.metrics_json, null, 2)}
          </pre>
        </details>
      )}
    </Card>
  );
}

/**
 * Spec 06 §3 Model Monitor (admin): every registered version with its test metrics and thresholds,
 * whether it is loaded and serving, drift status (from T5.8), and Deploy. Until hot reload (T5.8)
 * the API only accepts the loaded version and says why otherwise.
 */
export function ModelsPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const models = useQuery({ queryKey: MODELS_KEY, queryFn: listModels });
  const deploy = useMutation({
    mutationFn: (m: ModelVersion) => deployModel(m.version),
    onSuccess: (m) => {
      toast({ tone: "success", title: t("models.toast.deployed"), body: m.version });
      void queryClient.invalidateQueries({ queryKey: MODELS_KEY });
    },
    onError: (error) => toast({ tone: "error", title: t("models.toast.failed"), body: toApiError(error).message }),
  });

  if (models.isError) {
    return <ErrorState title={t("models.error")} message={toApiError(models.error).message} onRetry={() => void models.refetch()} className="min-h-72" />;
  }
  if (!models.data) {
    return <div className="space-y-4" aria-busy="true">{[0, 1].map((i) => <div key={i} className="h-56 animate-pulse rounded-xl bg-muted/70" />)}</div>;
  }
  if (models.data.items.length === 0) {
    return <EmptyState icon={Brain} title={t("models.empty")} className="min-h-60" />;
  }
  const sorted = [...models.data.items].sort((a, b) => Number(b.is_active) - Number(a.is_active) || Date.parse(b.created_at) - Date.parse(a.created_at));
  return (
    <div className="space-y-4">
      <p className="max-w-3xl text-sm text-muted-foreground">{t("models.intro")}</p>
      {sorted.map((m) => (
        <ModelCard key={m.id} model={m} deploying={deploy.isPending && deploy.variables.id === m.id} onDeploy={() => deploy.mutate(m)} />
      ))}
    </div>
  );
}
