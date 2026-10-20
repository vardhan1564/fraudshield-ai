import { useTranslation } from "react-i18next";
import { Navigate, Outlet, useLocation } from "react-router-dom";

import { FullPageSpinner } from "@/shared/components/EmptyState";
import { useAuth } from "@/shared/hooks/useAuth";
import { homeFor } from "@/shared/lib/roles";
import type { Role } from "@/shared/types/api";

/** Protects a route tree: logged-out users go to /login (and come back after), a wrong role
 * goes to its own home. */
export function RequireAuth({ roles }: { roles: Role[] }) {
  const { state } = useAuth();
  const { t } = useTranslation();
  const location = useLocation();

  if (state.status === "loading") return <FullPageSpinner label={t("common.loading")} />;
  if (state.status === "anonymous") {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  if (!roles.includes(state.user.role)) return <Navigate to={homeFor(state.user.role)} replace />;
  return <Outlet />;
}
