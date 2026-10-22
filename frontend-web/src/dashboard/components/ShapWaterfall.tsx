import { useSyncExternalStore } from "react";
import { useTranslation } from "react-i18next";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  LabelList,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { ChartCard, DataTable } from "@/dashboard/components/ChartCard";
import { formatImpact, type WaterfallStep, waterfallSteps } from "@/dashboard/lib/waterfall";
import type { Reason } from "@/shared/types/api";

const ROW_PX = 34;
const WIDE = "(min-width: 640px)";
const FILL: Record<WaterfallStep["kind"], string> = {
  raises: "var(--chart-raises)",
  lowers: "var(--chart-lowers)",
  total: "var(--muted-foreground)",
};

function short(text: string, chars: number): string {
  return text.length > chars ? `${text.slice(0, chars - 1)}…` : text;
}

/** Phones get a narrower label column so the bars and their values still fit. */
function useWide(): boolean {
  return useSyncExternalStore(
    (onChange) => {
      const query = window.matchMedia?.(WIDE);
      query?.addEventListener("change", onChange);
      return () => query?.removeEventListener("change", onChange);
    },
    () => window.matchMedia?.(WIDE).matches ?? true,
  );
}

function useValueText() {
  const { t } = useTranslation();
  return (value: WaterfallStep["value"]): string => {
    if (value === null) return "";
    if (typeof value === "boolean") return value ? t("case.shap.yes") : t("case.shap.no");
    if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(2);
    return value;
  };
}

function StepTooltip({ active, step }: { active?: boolean; step: WaterfallStep | undefined }) {
  const { t } = useTranslation();
  const valueText = useValueText();
  if (!active || !step) return null;
  return (
    <div className="max-w-72 rounded-lg border bg-card px-3 py-2 text-sm shadow-lg">
      <p className="font-semibold tabular-nums">{formatImpact(step.impact)}</p>
      <p>{step.label}</p>
      {step.kind !== "total" && (
        <p className="mt-0.5 text-xs text-muted-foreground">
          <span className="font-mono">{step.feature}</span> = {valueText(step.value)} · {t(`case.shap.${step.kind}`)}
        </p>
      )}
    </div>
  );
}

/** Signed value at the outer end of each bar: right of a push up, left of a push down. */
function ImpactLabel(props: {
  x?: number | string;
  y?: number | string;
  width?: number | string;
  height?: number | string;
  index?: number;
  steps: WaterfallStep[];
}) {
  const step = props.index === undefined ? undefined : props.steps[props.index];
  if (!step) return null;
  const [x, y, w, h] = [props.x, props.y, props.width, props.height].map(Number) as [number, number, number, number];
  const left = step.kind === "lowers";
  return (
    <text
      x={left ? x - 6 : x + w + 6}
      y={y + h / 2}
      dominantBaseline="central"
      textAnchor={left ? "end" : "start"}
      fontSize={12}
      fill="var(--muted-foreground)"
      style={{ fontVariantNumeric: "tabular-nums", fontWeight: step.kind === "total" ? 600 : 400 }}
    >
      {formatImpact(step.impact)}
    </text>
  );
}

/**
 * Spec 06 Case Detail: why the model scored this payment the way it did. The 10 biggest SHAP
 * pushes (meta-model log-odds) as a waterfall (red raises risk, blue lowers it), then the net
 * effect; the same numbers as a table.
 */
export function ShapWaterfall({ shap }: { shap: readonly Reason[] }) {
  const { t } = useTranslation();
  const valueText = useValueText();
  const wide = useWide();
  const steps = waterfallSteps(shap, t("case.shap.net"));
  const lows = steps.map((s) => s.range[0]);
  const highs = steps.map((s) => s.range[1]);
  const min = Math.min(0, ...lows);
  const max = Math.max(0, ...highs);
  const pad = Math.max((max - min) * (wide ? 0.16 : 0.42), 0.2); // room for the value labels at both ends

  const chart = () => (
    <div>
    <ul className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
      {(["raises", "lowers"] as const).map((kind) => (
        <li key={kind} className="inline-flex items-center gap-1.5">
          <span className="size-2.5 rounded-sm" style={{ background: FILL[kind] }} aria-hidden="true" />
          {t(`case.shap.${kind}`)}
        </li>
      ))}
    </ul>
    <div style={{ height: steps.length * ROW_PX + 12 }} aria-label={t("case.shap.title")}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={steps} layout="vertical" margin={{ top: 4, right: 4, bottom: 4, left: 0 }} barCategoryGap={7}>
          <CartesianGrid horizontal={false} stroke="var(--border)" />
          <XAxis type="number" hide domain={[min - pad, max + pad]} />
          <YAxis
            type="category"
            dataKey="key"
            width={wide ? 250 : 124}
            interval={0}
            tickFormatter={(key: string) => short(steps.find((s) => s.key === key)?.label ?? "", wide ? 38 : 22)}
            tick={{ fontSize: wide ? 12.5 : 11.5, fill: "var(--foreground)" }}
            axisLine={false}
            tickLine={false}
          />
          <ReferenceLine x={0} stroke="var(--muted-foreground)" strokeOpacity={0.5} />
          <Tooltip
            content={(p) => <StepTooltip active={p.active} step={p.payload[0]?.payload as WaterfallStep | undefined} />}
            cursor={{ fill: "var(--muted)", opacity: 0.6 }}
            isAnimationActive={false}
          />
          <Bar dataKey="range" barSize={18} radius={4} isAnimationActive={false}>
            {steps.map((s) => (
              <Cell key={s.key} fill={FILL[s.kind]} />
            ))}
            <LabelList dataKey="range" content={(p) => <ImpactLabel {...p} steps={steps} />} />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
    </div>
  );

  // the running total is where each bar ends: its top for a push up, its bottom for a push down
  const rows = steps.map((s) => [
    s.label,
    valueText(s.value),
    formatImpact(s.impact),
    s.kind === "total" ? "" : formatImpact(s.kind === "raises" ? s.range[1] : s.range[0]),
  ]);
  const table = () =>
    steps.length === 0 ? (
      <p className="py-8 text-center text-sm text-muted-foreground">{t("case.shap.empty")}</p>
    ) : (
      <DataTable head={[t("case.shap.signal"), t("case.shap.value"), t("case.shap.push"), t("case.shap.running")]} rows={rows} />
    );

  return (
    <ChartCard
      title={t("case.shap.title")}
      subtitle={t("case.shap.subtitle")}
      chart={steps.length === 0 ? undefined : chart}
      table={table}
    />
  );
}
