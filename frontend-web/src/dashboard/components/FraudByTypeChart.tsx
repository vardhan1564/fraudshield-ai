import { useTranslation } from "react-i18next";
import { Bar, BarChart, CartesianGrid, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { formatNumber } from "@/shared/lib/format";

export interface TypeRow {
  key: string;
  name: string;
  count: number;
}

const ROW_PX = 40;

function TypeTooltip({ active, row }: { active?: boolean; row: TypeRow | undefined }) {
  if (!active || !row) return null;
  return (
    <div className="rounded-lg border bg-card px-3 py-2 text-sm shadow-lg">
      <p className="font-semibold tabular-nums">{formatNumber(row.count)}</p>
      <p className="text-muted-foreground">{row.name}</p>
    </div>
  );
}

/** Horizontal bars, one colour (single series: no legend), largest first, value at the tip. */
export function FraudByTypeChart({ rows, label, totalLabel }: { rows: TypeRow[]; label?: string; totalLabel?: string }) {
  const { t } = useTranslation();
  const max = Math.max(...rows.map((r) => r.count), 1);
  const total = rows.reduce((sum, r) => sum + r.count, 0);
  // Room for the value at the tip of the longest bar ("46,382"): never clipped.
  const labelRoom = formatNumber(max).length * 8 + 12;
  return (
    <div>
    <div style={{ height: rows.length * ROW_PX + 8 }} aria-label={label ?? t("overview.types.title")}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows} layout="vertical" margin={{ top: 0, right: labelRoom, bottom: 0, left: 0 }} barCategoryGap={8}>
          <CartesianGrid horizontal={false} stroke="var(--border)" />
          <XAxis type="number" hide domain={[0, max]} />
          <YAxis
            type="category"
            dataKey="name"
            width={156}
            tick={{ fontSize: 13, fill: "var(--foreground)" }}
            axisLine={false}
            tickLine={false}
          />
          <Tooltip
            content={(p) => <TypeTooltip active={p.active} row={p.payload[0]?.payload as TypeRow | undefined} />}
            cursor={{ fill: "var(--muted)", opacity: 0.6 }}
            isAnimationActive={false}
          />
          <Bar dataKey="count" fill="var(--chart-flagged)" barSize={20} radius={[0, 4, 4, 0]} isAnimationActive={false}>
            <LabelList
              dataKey="count"
              position="right"
              formatter={(v: unknown) => formatNumber(Number(v))}
              style={{ fontSize: 12, fill: "var(--muted-foreground)", fontVariantNumeric: "tabular-nums" }}
            />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
    <p className="mt-4 flex items-baseline justify-between border-t pt-3 text-sm">
      <span className="text-muted-foreground">{totalLabel ?? t("overview.types.total")}</span>
      <span className="font-semibold tabular-nums">{formatNumber(total)}</span>
    </p>
    </div>
  );
}
