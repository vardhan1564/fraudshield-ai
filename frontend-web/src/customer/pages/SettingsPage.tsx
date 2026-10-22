import { LogOut } from "lucide-react";
import { useTranslation } from "react-i18next";

import { ContextPanel } from "@/customer/components/ContextPanel";
import { LanguageSwitcher } from "@/shared/components/LanguageSwitcher";
import { ThemeToggle } from "@/shared/components/ThemeToggle";
import { Button } from "@/shared/components/ui/button";
import { useAuth } from "@/shared/hooks/useAuth";
import { maskPhone } from "@/shared/lib/format";

/** Spec 07 §3 Settings: language (saved to the profile), theme, the Simulate context panel, logout. */
export function SettingsPage() {
  const { t } = useTranslation();
  const { state, logout } = useAuth();
  if (state.status !== "authenticated") return null;
  return (
    <div className="space-y-6 px-5 py-4">
      <h1 className="text-2xl font-semibold tracking-tight">{t("nav.settings")}</h1>
      <section className="rounded-2xl border bg-card p-5">
        <p className="text-lg font-semibold">{state.user.name}</p>
        <p className="mt-0.5 text-base text-muted-foreground">{maskPhone(state.user.phone)}</p>
      </section>
      <section className="space-y-3 rounded-2xl border bg-card p-5">
        <div className="flex min-h-11 items-center justify-between gap-3">
          <span className="text-base font-medium">{t("common.language")}</span>
          <LanguageSwitcher />
        </div>
        <div className="flex min-h-11 items-center justify-between gap-3">
          <span className="text-base font-medium">{t("common.theme")}</span>
          <ThemeToggle />
        </div>
      </section>
      <ContextPanel />
      <Button variant="outline" size="lg" className="w-full text-base" onClick={logout}>
        <LogOut aria-hidden="true" />
        {t("common.logout")}
      </Button>
    </div>
  );
}
