import { CircleAlert, CircleCheck, type LucideIcon } from "lucide-react";

import { Card } from "@/shared/components/ui/card";
import { cn } from "@/shared/lib/cn";

export interface KpiCardProps {
  label: string;
  /** Already formatted ("₹38.45L", "12,480", "0.64%"); null shows a dash. */
  value: string | null;
  /** Full value for the tooltip when `value` is compact. */
  fullValue?: string;
  hint?: string;
  icon: LucideIcon;
  /** Status always comes with an icon and words, never colour alone (dataviz rule). */
  status?: { tone: "good" | "critical"; label: string };
  loading?: boolean;
}

/** One headline number (spec 06 §3 Overview). Stat-tile anatomy: label, value, hint. */
export function KpiCard({ label, value, fullValue, hint, icon: Icon, status, loading = false }: KpiCardProps) {
  if (loading) {
    return (
      <Card className="p-5" aria-busy="true" data-testid="kpi-skeleton">
        <div className="flex items-center justify-between">
          <div className="h-4 w-28 animate-pulse rounded bg-muted" />
          <div className="size-9 animate-pulse rounded-lg bg-muted" />
        </div>
        <div className="mt-4 h-8 w-24 animate-pulse rounded bg-muted" />
        <div className="mt-3 h-3.5 w-36 animate-pulse rounded bg-muted" />
      </Card>
    );
  }
  const StatusIcon = status?.tone === "critical" ? CircleAlert : CircleCheck;
  return (
    <Card className="flex flex-col p-5">
      <div className="flex items-start justify-between gap-3">
        <p className="text-sm font-medium text-muted-foreground">{label}</p>
        <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-secondary text-primary">
          <Icon className="size-[18px]" aria-hidden="true" />
        </span>
      </div>
      <p className="mt-2 text-[28px] font-semibold leading-tight tracking-tight" title={fullValue}>
        {value ?? "—"}
      </p>
      {status ? (
        <p
          className={cn(
            "mt-1.5 inline-flex items-center gap-1.5 text-[13px] font-medium",
            status.tone === "critical" ? "text-risk-high" : "text-risk-low",
          )}
        >
          <StatusIcon className="size-4 shrink-0" aria-hidden="true" />
          {status.label}
        </p>
      ) : (
        hint && <p className="mt-1.5 text-[13px] text-muted-foreground">{hint}</p>
      )}
    </Card>
  );
}
