import { FlaskConical, PhoneCall, RotateCcw } from "lucide-react";
import { useId } from "react";
import { useTranslation } from "react-i18next";

import { CALL_MINUTES, NORMAL, REMOTE_APPS, type RemoteApp, SCAM_CALL } from "@/customer/state/simContext";
import { useSimContext } from "@/customer/state/useSimContext";
import { Button } from "@/shared/components/ui/button";
import { cn } from "@/shared/lib/cn";

/** A big on/off switch: the whole row is the tap target (≥ 44 px, spec 07 §4). */
function SwitchRow({ label, hint, checked, onChange }: {
  label: string;
  hint?: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  const id = useId();
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-describedby={hint ? id : undefined}
      onClick={() => onChange(!checked)}
      className="flex min-h-14 w-full cursor-pointer items-center justify-between gap-4 py-2 text-left"
    >
      <span className="min-w-0">
        <span className="block text-base font-medium">{label}</span>
        {hint && <span id={id} className="block text-base text-muted-foreground">{hint}</span>}
      </span>
      <span
        aria-hidden="true"
        className={cn(
          "relative inline-flex h-8 w-14 shrink-0 items-center rounded-full transition-colors",
          checked ? "bg-primary" : "bg-muted-foreground/35",
        )}
      >
        <span className={cn("size-6 rounded-full bg-white shadow transition-transform", checked ? "translate-x-7" : "translate-x-1")} />
      </span>
    </button>
  );
}

/**
 * Settings › Simulate context (spec 07 §1, §3): the phone signals a browser can't read (call,
 * caller, screen share, remote-access app, VPN). Every payment sends them as `context`.
 */
export function ContextPanel() {
  const { t } = useTranslation();
  const { sim, update, set } = useSimContext();
  const minutesId = useId();
  const remoteId = useId();

  return (
    <section aria-labelledby="sim-title" className="rounded-2xl border bg-card p-5">
      <h2 id="sim-title" className="flex items-center gap-2 text-lg font-semibold">
        <FlaskConical className="size-5 text-primary" aria-hidden="true" />
        {t("customer.sim.title")}
      </h2>
      <p className="mt-1 text-base text-muted-foreground">{t("customer.sim.body")}</p>

      <div className="mt-3 divide-y">
        <SwitchRow
          label={t("customer.sim.usualPhone")}
          hint={t("customer.sim.usualPhoneHint")}
          checked={sim.usualPhone}
          onChange={(usualPhone) => update({ usualPhone })}
        />
        <SwitchRow label={t("customer.sim.onCall")} checked={sim.onCall} onChange={(onCall) => update({ onCall })} />
        {sim.onCall && (
          <div className="space-y-3 pb-3 pl-4">
            <SwitchRow
              label={t("customer.sim.unknownCaller")}
              hint={t("customer.sim.unknownCallerHint")}
              checked={sim.unknownCaller}
              onChange={(unknownCaller) => update({ unknownCaller })}
            />
            <div role="radiogroup" aria-labelledby={minutesId}>
              <p id={minutesId} className="mb-2 text-base font-medium">{t("customer.sim.callLength")}</p>
              <div className="grid grid-cols-3 gap-2">
                {CALL_MINUTES.map((m) => (
                  <button
                    key={m}
                    type="button"
                    role="radio"
                    aria-checked={sim.callMinutes === m}
                    onClick={() => update({ callMinutes: m })}
                    className={cn(
                      "min-h-11 cursor-pointer rounded-xl border text-base font-medium",
                      sim.callMinutes === m ? "border-primary bg-secondary text-secondary-foreground" : "bg-card",
                    )}
                  >
                    {t("customer.sim.minutes", { count: m })}
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}
        <SwitchRow label={t("customer.sim.screenShare")} checked={sim.screenShare} onChange={(screenShare) => update({ screenShare })} />
        <div className="flex min-h-14 items-center justify-between gap-4 py-2">
          <label htmlFor={remoteId} className="text-base font-medium">{t("customer.sim.remoteApp")}</label>
          <select
            id={remoteId}
            value={sim.remoteApp ?? ""}
            onChange={(e) => update({ remoteApp: (e.target.value || null) as RemoteApp | null })}
            className="min-h-11 rounded-xl border bg-card px-3 text-base"
          >
            <option value="">{t("customer.sim.none")}</option>
            {REMOTE_APPS.map((app) => (
              <option key={app} value={app}>{app}</option>
            ))}
          </select>
        </div>
        <SwitchRow label={t("customer.sim.vpn")} checked={sim.vpn} onChange={(vpn) => update({ vpn })} />
      </div>

      <div className="mt-4 grid grid-cols-2 gap-2">
        <Button variant="outline" size="lg" className="text-base" onClick={() => set(SCAM_CALL)}>
          <PhoneCall aria-hidden="true" />
          {t("customer.sim.scamCall")}
        </Button>
        <Button variant="outline" size="lg" className="text-base" onClick={() => set(NORMAL)}>
          <RotateCcw aria-hidden="true" />
          {t("customer.sim.reset")}
        </Button>
      </div>
    </section>
  );
}
