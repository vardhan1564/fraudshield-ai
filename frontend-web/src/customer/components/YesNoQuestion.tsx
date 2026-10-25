import { Check, X } from "lucide-react";
import { useTranslation } from "react-i18next";

import { cn } from "@/shared/lib/cn";

/**
 * One Scam Coach question (spec 07 §3) with two big Yes / No buttons: a radio group, so screen
 * readers announce the question and which answer is chosen. Icons always come with a label.
 */
export function YesNoQuestion({
  id,
  number,
  question,
  value,
  onChange,
  disabled = false,
}: {
  id: string;
  number: number;
  question: string;
  value: boolean | null;
  onChange: (value: boolean) => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const option = (answer: boolean) => {
    const chosen = value === answer;
    const Icon = answer ? Check : X;
    return (
      <button
        type="button"
        role="radio"
        aria-checked={chosen}
        disabled={disabled}
        onClick={() => onChange(answer)}
        className={cn(
          "flex min-h-14 flex-1 cursor-pointer items-center justify-center gap-2 rounded-xl border-2 text-lg font-semibold transition-colors",
          "focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/30 disabled:cursor-not-allowed disabled:opacity-60",
          chosen ? "border-primary bg-primary text-primary-foreground" : "border-input bg-card hover:bg-muted",
        )}
      >
        <Icon className="size-5" aria-hidden="true" />
        {answer ? t("customer.coach.yes") : t("customer.coach.no")}
      </button>
    );
  };
  return (
    <fieldset className="rounded-2xl border bg-card p-4">
      <legend className="sr-only">{question}</legend>
      <p id={`${id}-label`} className="text-lg font-semibold leading-snug" aria-hidden="true">
        <span className="text-muted-foreground">{number}.</span>{" "}
        {question}
      </p>
      <div role="radiogroup" aria-labelledby={`${id}-label`} className="mt-3 flex gap-3">
        {option(true)}
        {option(false)}
      </div>
    </fieldset>
  );
}
