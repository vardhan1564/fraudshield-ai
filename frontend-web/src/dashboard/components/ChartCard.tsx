import { BarChart3, Table2 } from "lucide-react";
import { type ReactNode, useState } from "react";
import { useTranslation } from "react-i18next";

import { Card } from "@/shared/components/ui/card";
import { cn } from "@/shared/lib/cn";

/**
 * A titled card that shows a chart, or the same numbers as a table (dataviz rule: a tooltip
 * never gates a value, there is always a table view). `chart` and `table` render on demand.
 */
export function ChartCard({
  title,
  subtitle,
  chart,
  table,
  className,
  dimmed = false,
}: {
  title: string;
  subtitle?: string;
  chart?: () => ReactNode;
  table: () => ReactNode;
  className?: string;
  /** Refetching: keep the frame, fade it (no skeleton flash). */
  dimmed?: boolean;
}) {
  const { t } = useTranslation();
  const [view, setView] = useState<"chart" | "table">(chart ? "chart" : "table");
  return (
    <Card className={cn("flex flex-col", className)}>
      <div className="flex items-start justify-between gap-3 px-5 pb-2 pt-5">
        <div className="min-w-0">
          <h2 className="text-[15px] font-semibold">{title}</h2>
          {subtitle && <p className="mt-0.5 text-[13px] text-muted-foreground">{subtitle}</p>}
        </div>
        {chart && (
          <div className="flex shrink-0 rounded-lg border p-0.5" role="group" aria-label={title}>
            {(["chart", "table"] as const).map((v) => (
              <button
                key={v}
                type="button"
                onClick={() => setView(v)}
                aria-pressed={view === v}
                className={cn(
                  "inline-flex h-7 cursor-pointer items-center gap-1.5 rounded-md px-2.5 text-xs font-medium transition-colors",
                  view === v ? "bg-secondary text-secondary-foreground" : "text-muted-foreground hover:text-foreground",
                )}
              >
                {v === "chart" ? <BarChart3 className="size-3.5" aria-hidden="true" /> : <Table2 className="size-3.5" aria-hidden="true" />}
                {t(v === "chart" ? "overview.chart" : "overview.table")}
              </button>
            ))}
          </div>
        )}
      </div>
      <div className={cn("flex-1 px-5 pb-5 transition-opacity", dimmed && "opacity-60")}>
        {view === "chart" && chart ? chart() : table()}
      </div>
    </Card>
  );
}

/** Plain numeric table used by every chart's table view. */
export function DataTable({ head, rows }: { head: string[]; rows: (string | number)[][] }) {
  return (
    <div className="max-h-80 overflow-auto rounded-lg border">
      <table className="w-full text-sm">
        <thead className="sticky top-0 bg-muted/80 backdrop-blur">
          <tr>
            {head.map((h, i) => (
              <th key={h} scope="col" className={cn("px-3 py-2 font-medium text-muted-foreground", i === 0 ? "text-left" : "text-right")}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row[0])} className="border-t">
              {row.map((cell, i) => (
                <td key={i} className={cn("px-3 py-1.5", i === 0 ? "text-left" : "text-right tabular-nums")}>
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
