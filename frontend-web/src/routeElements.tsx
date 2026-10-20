import { useTranslation } from "react-i18next";
import { Navigate } from "react-router-dom";

import { CUSTOMER_NAV } from "@/customer/layout/customerNav";
import { CustomerComingSoon } from "@/customer/pages/CustomerComingSoon";
import { FullPageSpinner } from "@/shared/components/EmptyState";
import { useAuth } from "@/shared/hooks/useAuth";
import { homeFor } from "@/shared/lib/roles";

/** `/` → the signed-in user's home, else the login page. */
export function RootRedirect() {
  const { state } = useAuth();
  const { t } = useTranslation();
  if (state.status === "loading") return <FullPageSpinner label={t("common.loading")} />;
  return <Navigate to={state.status === "authenticated" ? homeFor(state.user.role) : "/login"} replace />;
}

export function CustomerPlaceholder({ index }: { index: number }) {
  const { t } = useTranslation();
  const item = CUSTOMER_NAV[index];
  return item ? <CustomerComingSoon task={item.task} title={t(item.labelKey)} /> : null;
}
