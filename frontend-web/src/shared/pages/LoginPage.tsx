import { Eye, EyeOff, Gauge, MessageSquareText, ShieldCheck, TriangleAlert } from "lucide-react";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";
import { Navigate, useLocation, useNavigate } from "react-router-dom";

import { toApiError } from "@/shared/api/client";
import { LanguageSwitcher } from "@/shared/components/LanguageSwitcher";
import { Logo } from "@/shared/components/Logo";
import { ThemeToggle } from "@/shared/components/ThemeToggle";
import { Button } from "@/shared/components/ui/button";
import { Input } from "@/shared/components/ui/input";
import { Label } from "@/shared/components/ui/label";
import { useAuth } from "@/shared/hooks/useAuth";
import { DEMO_ACCOUNTS, DEMO_PASSWORD, IS_MOCK } from "@/shared/lib/demo";
import { homeFor } from "@/shared/lib/roles";
import type { Role } from "@/shared/types/api";

const KNOWN_ERRORS = new Set(["UNAUTHORIZED", "RATE_LIMITED", "NETWORK_ERROR"]);

/** Go back where the user was headed, if their role may see it; else their home. */
function destination(role: Role, from: string | undefined): string {
  const home = homeFor(role);
  return from?.startsWith(home) ? from : home;
}

export function LoginPage() {
  const { t } = useTranslation();
  const { state, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from;

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  if (state.status === "authenticated") {
    return <Navigate to={destination(state.user.role, from)} replace />;
  }

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const user = await login({ email: email.trim(), password });
      navigate(destination(user.role, from), { replace: true });
    } catch (err) {
      const apiErr = toApiError(err);
      setError(
        KNOWN_ERRORS.has(apiErr.code)
          ? t(`errors.${apiErr.code as "UNAUTHORIZED" | "RATE_LIMITED" | "NETWORK_ERROR"}`)
          : t("errors.generic"),
      );
      setSubmitting(false);
    }
  }

  const points = [
    { icon: Gauge, text: t("login.sidePoints.speed") },
    { icon: MessageSquareText, text: t("login.sidePoints.explain") },
    { icon: ShieldCheck, text: t("login.sidePoints.scams") },
  ];

  return (
    <div className="grid min-h-dvh lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
      {/* Brand panel: desktop only */}
      <aside className="relative hidden overflow-hidden bg-primary text-primary-foreground lg:flex lg:flex-col lg:justify-between lg:p-12">
        <div
          aria-hidden="true"
          className="absolute -right-32 -top-32 size-[28rem] rounded-full bg-white/10 blur-3xl"
        />
        <div
          aria-hidden="true"
          className="absolute -bottom-40 -left-24 size-[26rem] rounded-full bg-black/10 blur-3xl"
        />
        <Logo className="relative [&_path:first-child]:fill-white [&_path:last-child]:stroke-primary" />
        <div className="relative max-w-md">
          <h1 className="text-4xl font-semibold leading-tight tracking-tight">{t("app.tagline")}</h1>
          <ul className="mt-10 space-y-5">
            {points.map(({ icon: Icon, text }) => (
              <li key={text} className="flex items-start gap-3.5">
                <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-white/15">
                  <Icon className="size-[18px]" aria-hidden="true" />
                </span>
                <span className="pt-1.5 text-[15px] text-primary-foreground/90">{text}</span>
              </li>
            ))}
          </ul>
        </div>
        <p className="relative text-sm text-primary-foreground/70">© FraudShield AI</p>
      </aside>

      {/* Form */}
      <main className="flex flex-col px-5 py-6 sm:px-10">
        <div className="flex items-center justify-between gap-3">
          <Logo className="lg:invisible" />
          <div className="flex items-center gap-2">
            <LanguageSwitcher />
            <ThemeToggle />
          </div>
        </div>

        <div className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center py-10">
          <h2 className="text-2xl font-semibold tracking-tight">{t("login.title")}</h2>
          <p className="mt-1.5 text-sm text-muted-foreground">{t("login.subtitle")}</p>

          <form onSubmit={onSubmit} className="mt-8 space-y-5" noValidate>
            {error && (
              <div
                role="alert"
                className="flex items-start gap-2.5 rounded-lg border border-destructive/25 bg-destructive/8 px-3.5 py-3 text-sm text-destructive"
              >
                <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
                <span>{error}</span>
              </div>
            )}
            <div className="space-y-2">
              <Label htmlFor="email">{t("login.email")}</Label>
              <Input
                id="email"
                type="email"
                autoComplete="username"
                inputMode="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                aria-invalid={error ? true : undefined}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="password">{t("login.password")}</Label>
              <div className="relative">
                <Input
                  id="password"
                  type={showPassword ? "text" : "password"}
                  autoComplete="current-password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="pr-11"
                  aria-invalid={error ? true : undefined}
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((v) => !v)}
                  className="absolute inset-y-0 right-0 grid w-11 cursor-pointer place-items-center rounded-r-lg text-muted-foreground hover:text-foreground"
                  aria-label={showPassword ? t("login.hidePassword") : t("login.showPassword")}
                >
                  {showPassword ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
                </button>
              </div>
            </div>
            <Button
              type="submit"
              size="lg"
              className="w-full"
              disabled={submitting || !email || !password}
            >
              {submitting ? t("login.submitting") : t("login.submit")}
            </Button>
          </form>

          {IS_MOCK && (
            <section className="mt-8 rounded-xl border bg-muted/50 p-4">
              <h3 className="text-sm font-medium">{t("login.demoTitle")}</h3>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {t("login.demoHint", { password: DEMO_PASSWORD })}
              </p>
              <div className="mt-3 grid gap-2 sm:grid-cols-3">
                {DEMO_ACCOUNTS.map((a) => (
                  <Button
                    key={a.email}
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      setEmail(a.email);
                      setPassword(DEMO_PASSWORD);
                    }}
                  >
                    {t(`roles.${a.role}`)}
                  </Button>
                ))}
              </div>
            </section>
          )}
        </div>
      </main>
    </div>
  );
}
