import { Hourglass } from "lucide-react";

import { formatRemaining } from "@/customer/lib/coach";
import { useNow } from "@/shared/hooks/useNow";
import { cn } from "@/shared/lib/cn";

/**
 * The Scam Coach's cooling-off time (spec 07 §3): a calm countdown, not an alarm. Shows
 * `running` with the time left, then `done` once `endsAt` has passed.
 */
export function CountdownTimer({
  endsAt,
  running,
  done,
  className,
}: {
  endsAt: number;
  running: (left: string) => string;
  done: string;
  className?: string;
}) {
  const now = useNow(1_000);
  const left = endsAt - now;
  return (
    <p
      role="timer"
      aria-live="off"
      className={cn("flex items-center gap-2.5 rounded-2xl bg-muted px-4 py-3 text-base", className)}
    >
      <Hourglass className="size-5 shrink-0 text-muted-foreground" aria-hidden="true" />
      {left > 0 ? (
        <span>
          {running(formatRemaining(left))}
        </span>
      ) : (
        <span className="font-medium">{done}</span>
      )}
    </p>
  );
}
