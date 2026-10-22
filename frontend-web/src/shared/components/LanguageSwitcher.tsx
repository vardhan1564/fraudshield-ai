import { Languages } from "lucide-react";
import { useTranslation } from "react-i18next";

import { updateMe } from "@/shared/api/endpoints/auth";
import { useAuth } from "@/shared/hooks/useAuth";
import { LANGUAGES, setLanguage } from "@/shared/i18n";
import { cn } from "@/shared/lib/cn";
import type { Language } from "@/shared/types/api";

/**
 * Native select: accessible, works the same on desktop and phones. Signed in, the choice is
 * also saved to the profile, so it survives a reload and the risk warnings use it (T4.5).
 */
export function LanguageSwitcher({ className }: { className?: string }) {
  const { t, i18n } = useTranslation();
  const { state } = useAuth();
  const change = (language: Language) => {
    setLanguage(language);
    if (state.status === "authenticated") void updateMe({ language }).catch(() => undefined); // the screen already switched
  };
  return (
    <label
      className={cn(
        "relative inline-flex h-10 items-center gap-2 rounded-lg border bg-card pl-3 pr-2 text-sm " +
          "text-muted-foreground transition-colors hover:text-foreground focus-within:ring-3 " +
          "focus-within:ring-ring/20",
        className,
      )}
    >
      <Languages className="size-4" aria-hidden="true" />
      <span className="sr-only">{t("common.language")}</span>
      <select
        value={i18n.resolvedLanguage ?? "en"}
        onChange={(e) => change(e.target.value as Language)}
        className="cursor-pointer appearance-none bg-transparent pr-1 font-medium text-foreground outline-none"
      >
        {LANGUAGES.map((l) => (
          <option key={l.code} value={l.code}>
            {l.label}
          </option>
        ))}
      </select>
    </label>
  );
}
