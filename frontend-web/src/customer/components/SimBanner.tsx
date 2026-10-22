import { FlaskConical } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { isActive } from "@/customer/state/simContext";
import { useSimContext } from "@/customer/state/useSimContext";

/** Says which simulated phone signals the next payment will send, so a demo never forgets them. */
export function SimBanner() {
  const { t } = useTranslation();
  const { sim } = useSimContext();
  if (!isActive(sim)) return null;
  const parts = [
    !sim.usualPhone && t("customer.sim.shortNewPhone"),
    sim.onCall && (sim.unknownCaller ? t("customer.sim.shortUnknownCall") : t("customer.sim.shortCall")),
    sim.screenShare && t("customer.sim.shortScreen"),
    sim.remoteApp,
    sim.vpn && t("customer.sim.vpn"),
  ].filter(Boolean);
  return (
    <Link
      to="/app/settings"
      className="flex min-h-11 items-center gap-2.5 rounded-xl border border-dashed border-primary/50 bg-secondary px-3.5 py-2 text-base"
    >
      <FlaskConical className="size-5 shrink-0 text-primary" aria-hidden="true" />
      <span>
        <span className="font-semibold">{t("customer.sim.active")}</span> {parts.join(" · ")}
      </span>
    </Link>
  );
}
