import { forwardRef, type InputHTMLAttributes } from "react";

import { cn } from "@/shared/lib/cn";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  ({ className, type = "text", ...props }, ref) => (
    <input
      ref={ref}
      type={type}
      className={cn(
        "flex h-11 w-full rounded-lg border border-input bg-card px-3.5 text-[15px] shadow-xs " +
          "transition-colors placeholder:text-muted-foreground/70 " +
          "focus-visible:border-ring focus-visible:outline-none focus-visible:ring-3 " +
          "focus-visible:ring-ring/20 disabled:cursor-not-allowed disabled:opacity-50 " +
          "aria-invalid:border-destructive aria-invalid:ring-destructive/15",
        className,
      )}
      {...props}
    />
  ),
);
Input.displayName = "Input";
