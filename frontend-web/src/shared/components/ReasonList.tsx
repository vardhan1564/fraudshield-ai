import { CircleAlert } from "lucide-react";

import { cn } from "@/shared/lib/cn";
import type { Reason } from "@/shared/types/api";

/** Plain-language reasons (spec 01 F4), icon + text; never the score or the SHAP numbers. */
export function ReasonList({ reasons, className }: { reasons: readonly Reason[]; className?: string }) {
  return (
    <ul className={cn("space-y-2.5", className)}>
      {reasons.map((r, i) => (
        <li key={`${r.feature}-${i}`} className="flex gap-3 text-base leading-snug">
          <CircleAlert className="mt-0.5 size-5 shrink-0" aria-hidden="true" />
          <span>{r.text}</span>
        </li>
      ))}
    </ul>
  );
}
