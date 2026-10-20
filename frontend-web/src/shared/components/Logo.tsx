import { cn } from "@/shared/lib/cn";

/** Shield mark + word mark. `compact` shows the mark only. */
export function Logo({
  className,
  compact = false,
  label = "FraudShield",
}: {
  className?: string;
  compact?: boolean;
  label?: string;
}) {
  return (
    <span className={cn("inline-flex items-center gap-2.5", className)}>
      <svg viewBox="0 0 32 32" className="size-8 shrink-0" aria-hidden="true">
        <path
          d="M16 2.5 4.5 7v8.2c0 7 4.9 12.6 11.5 14.3 6.6-1.7 11.5-7.3 11.5-14.3V7L16 2.5Z"
          className="fill-primary"
        />
        <path
          d="m10.5 16 3.8 3.8 7.2-7.6"
          fill="none"
          className="stroke-primary-foreground"
          strokeWidth="2.6"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
      {!compact && <span className="text-[17px] font-semibold tracking-tight">{label}</span>}
    </span>
  );
}
