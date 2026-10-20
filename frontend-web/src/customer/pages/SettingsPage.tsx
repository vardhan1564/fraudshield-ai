import { LogOut } from "lucide-react";
import { useTranslation } from "react-i18next";

import { LanguageSwitcher } from "@/shared/components/LanguageSwitcher";
import { ThemeToggle } from "@/shared/components/ThemeToggle";
import { Button } from "@/shared/components/ui/button";
import { useAuth } from "@/shared/hooks/useAuth";
import { maskPhone } from "@/shared/lib/format";

/** Language, theme and logout now; the Simulate-context panel comes in T4.5 (spec 07 §3). */
export function SettingsPage() {
  const { t } = useTranslation();
  const { state, logout } = useAuth();
  if (state.status !== "authenticated") return null;
  return (
    <div className="space-y-6 px-5 py-4">
      <h1 className="text-2xl font-semibold tracking-tight">{t("nav.settings")}</h1>
      <section className="rounded-2xl border bg-card p-5">
        <p className="text-lg font-semibold">{state.user.name}</p>
        <p className="mt-0.5 text-[15px] text-muted-foreground">{maskPhone(state.user.phone)}</p>
      </section>
      <section className="space-y-3 rounded-2xl border bg-card p-5">
        <div className="flex min-h-11 items-center justify-between gap-3">
          <span className="text-[15px] font-medium">{t("common.language")}</span>
          <LanguageSwitcher />
        </div>
        <div className="flex min-h-11 items-center justify-between gap-3">
          <span className="text-[15px] font-medium">{t("common.theme")}</span>
          <ThemeToggle />
        </div>
      </section>
      <Button variant="outline" size="lg" className="w-full" onClick={logout}>
        <LogOut aria-hidden="true" />
        {t("common.logout")}
      </Button>
    </div>
  );
}
