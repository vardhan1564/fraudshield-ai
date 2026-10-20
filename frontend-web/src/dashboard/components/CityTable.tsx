import { useTranslation } from "react-i18next";

import { formatNumber, formatPercent } from "@/shared/lib/format";
import type { MetricsOverview } from "@/shared/types/api";

/** Top cities: a table is the right form for ranked names + numbers; the inline bar shows share. */
export function CityTable({ rows }: { rows: MetricsOverview["by_city"] }) {
  const { t } = useTranslation();
  const total = rows.reduce((sum, r) => sum + r.flagged, 0) || 1;
  const max = Math.max(...rows.map((r) => r.flagged), 1);
  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="text-xs text-muted-foreground">
          <th scope="col" className="w-8 pb-2 text-left font-medium">#</th>
          <th scope="col" className="pb-2 text-left font-medium">{t("overview.cities.city")}</th>
          <th scope="col" className="pb-2 text-right font-medium">{t("overview.cities.flagged")}</th>
          <th scope="col" className="hidden w-[38%] pb-2 pl-4 text-left font-medium sm:table-cell">{t("overview.cities.share")}</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={r.city} className="border-t">
            <td className="py-2.5 text-muted-foreground tabular-nums">{i + 1}</td>
            <td className="py-2.5 font-medium">{r.city === "unknown" ? t("scenarios.unknown") : r.city}</td>
            <td className="py-2.5 text-right tabular-nums">{formatNumber(r.flagged)}</td>
            <td className="hidden py-2.5 pl-4 sm:table-cell">
              <div className="flex items-center gap-2">
                <div className="h-2 flex-1 rounded-full bg-muted">
                  <div
                    className="h-2 rounded-full bg-chart-flagged"
                    style={{ width: `${(r.flagged / max) * 100}%` }}
                  />
                </div>
                <span className="w-11 text-right text-xs text-muted-foreground tabular-nums">
                  {formatPercent(r.flagged / total, 0)}
                </span>
              </div>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
