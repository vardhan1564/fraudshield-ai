import { useTranslation } from "react-i18next";

import { RANGE_MS, type RangeKey } from "@/dashboard/components/range";
import { cn } from "@/shared/lib/cn";


/** Date-range presets: one row above everything they scope (dataviz filter rule). */
export function RangePicker({ value, onChange }: { value: RangeKey; onChange: (r: RangeKey) => void }) {
  const { t } = useTranslation();
  return (
    <div role="radiogroup" aria-label={t("overview.range.label")} className="inline-flex rounded-lg border bg-card p-1 shadow-xs">
      {(Object.keys(RANGE_MS) as RangeKey[]).map((key) => (
        <button
          key={key}
          type="button"
          role="radio"
          aria-checked={value === key}
          onClick={() => onChange(key)}
          className={cn(
            "h-8 cursor-pointer rounded-md px-3 text-[13px] font-medium transition-colors",
            value === key ? "bg-primary text-primary-foreground shadow-sm" : "text-muted-foreground hover:bg-muted hover:text-foreground",
          )}
        >
          {t(`overview.range.${key}`)}
        </button>
      ))}
    </div>
  );
}
