import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { formatCompact, formatNumber, formatPercent } from "@/shared/lib/format";
import { niceTicks } from "@/shared/lib/ticks";
import type { MetricsOverview } from "@/shared/types/api";

type Row = MetricsOverview["by_hour"][number];

const hourLabel = (h: number) => `${String(h).padStart(2, "0")}:00`;
const AXIS = { fontSize: 12, fill: "var(--muted-foreground)" };

/** One readout for the hovered hour, every series (dataviz: values lead, line keys). */
function HourTooltip({ active, label, rows }: { active?: boolean; label?: unknown; rows: Row[] }) {
  const { t } = useTranslation();
  const row = rows.find((r) => r.hour === label);
  if (!active || !row) return null;
  const items = [
    { key: "volume", color: "var(--chart-volume)", value: formatNumber(row.total), name: t("overview.hourly.volume") },
    { key: "flagged", color: "var(--chart-flagged)", value: formatNumber(row.flagged), name: t("overview.hourly.flagged") },
  ];
  return (
    <div className="rounded-lg border bg-card px-3 py-2.5 text-sm shadow-lg">
      <p className="mb-1.5 text-xs text-muted-foreground">
        {hourLabel(row.hour)}–{hourLabel((row.hour + 1) % 24)} IST
      </p>
      {items.map((i) => (
        <p key={i.key} className="flex items-center gap-2">
          <span className="h-0.5 w-3 rounded-full" style={{ background: i.color }} aria-hidden="true" />
          <span className="font-semibold tabular-nums">{i.value}</span>
          <span className="text-muted-foreground">{i.name}</span>
        </p>
      ))}
      <p className="mt-1 text-xs text-muted-foreground">
        {t("overview.hourly.rate")}: {row.total ? formatPercent(row.flagged / row.total, 1) : "—"}
      </p>
    </div>
  );
}

function Panel({ rows, dataKey, color, title, showAxis, hovered, onHover }: {
  rows: Row[];
  dataKey: "total" | "flagged";
  color: string;
  title: string;
  showAxis: boolean;
  /** The crosshair is synced across both panels; the tooltip shows only in the hovered one. */
  hovered: boolean;
  onHover: (on: boolean) => void;
}) {
  const ticks = niceTicks(Math.max(...rows.map((r) => r[dataKey])));
  return (
    <figure>
      <figcaption className="mb-1 text-xs font-medium text-muted-foreground">{title}</figcaption>
      <div className={showAxis ? "h-36" : "h-28"}>
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart
            data={rows}
            syncId="overview-hourly"
            margin={{ top: 8, right: 16, bottom: showAxis ? 0 : 8, left: 0 }}
            onMouseEnter={() => onHover(true)}
            onMouseLeave={() => onHover(false)}
          >
            <CartesianGrid vertical={false} stroke="var(--border)" />
            <XAxis
              dataKey="hour"
              tickFormatter={hourLabel}
              ticks={[0, 6, 12, 18]}
              tick={AXIS}
              axisLine={{ stroke: "var(--border)" }}
              tickLine={false}
              hide={!showAxis}
            />
            <YAxis
              ticks={ticks}
              interval={0}
              domain={[0, ticks[2]]}
              tickFormatter={formatCompact}
              tick={AXIS}
              axisLine={false}
              tickLine={false}
              width={40}
            />
            <Tooltip
              content={(p) => <HourTooltip active={p.active && hovered} label={p.label} rows={rows} />}
              cursor={{ stroke: "var(--muted-foreground)", strokeWidth: 1 }}
              isAnimationActive={false}
            />
            <Area
              type="monotone"
              dataKey={dataKey}
              stroke={color}
              strokeWidth={2}
              fill={color}
              fillOpacity={0.1}
              activeDot={{ r: 4, stroke: "var(--card)", strokeWidth: 2, fill: color }}
              isAnimationActive={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </figure>
  );
}

/**
 * Payments and flagged payments per IST hour as two small charts on the same x axis, NOT one
 * dual-axis chart: flagged is ~2% of volume, so a shared y scale would flatten it (dataviz).
 * The hover crosshair is synced across both.
 */
export function HourlyCharts({ rows }: { rows: Row[] }) {
  const { t } = useTranslation();
  const [hovered, setHovered] = useState<"total" | "flagged" | null>(null);
  const hover = (key: "total" | "flagged") => (on: boolean) => setHovered(on ? key : null);
  return (
    <div className="space-y-3">
      <Panel rows={rows} dataKey="total" color="var(--chart-volume)" title={t("overview.hourly.volume")}
        showAxis={false} hovered={hovered === "total"} onHover={hover("total")} />
      <Panel rows={rows} dataKey="flagged" color="var(--chart-flagged)" title={t("overview.hourly.flagged")}
        showAxis hovered={hovered === "flagged"} onHover={hover("flagged")} />
    </div>
  );
}
