import { ShieldCheck } from "lucide-react";
import { useTranslation } from "react-i18next";

import { CustomerComingSoon } from "@/customer/pages/CustomerComingSoon";
import { Logo } from "@/shared/components/Logo";
import { useAuth } from "@/shared/hooks/useAuth";

/** Shell of the customer Home (balance and recent payments come in T4.5). */
export function HomePage() {
  const { t } = useTranslation();
  const { state } = useAuth();
  const firstName = state.status === "authenticated" ? (state.user.name.split(" ")[0] ?? "") : "";
  return (
    <div className="space-y-5 px-5 pb-6 pt-4">
      <header className="flex items-center justify-between">
        <Logo label={t("app.customerName")} />
      </header>
      <h1 className="text-2xl font-semibold tracking-tight">{t("customer.hello", { name: firstName })}</h1>
      <section className="flex gap-4 rounded-2xl bg-primary p-5 text-primary-foreground shadow-sm">
        <ShieldCheck className="size-8 shrink-0" aria-hidden="true" />
        <div>
          <h2 className="text-lg font-semibold">{t("customer.protected")}</h2>
          <p className="mt-1 text-[15px] leading-relaxed text-primary-foreground/90">
            {t("customer.protectedBody")}
          </p>
        </div>
      </section>
      <CustomerComingSoon task="T4.5" inset />
    </div>
  );
}
