import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { PhoneFrame } from "@/customer/layout/PhoneFrame";
import { PayPage } from "@/customer/pages/PayPage";
import { PaymentDonePage } from "@/customer/pages/PaymentDonePage";
import { RiskWarningPage } from "@/customer/pages/RiskWarningPage";
import { ScamCoachPage } from "@/customer/pages/ScamCoachPage";
import { SettingsPage } from "@/customer/pages/SettingsPage";
import { server } from "@/mocks/node";
import { API_URL } from "@/shared/api/client";
import type { CoachRequest, CoachResponse, ScoreRequest, ScoreResponse } from "@/shared/types/api";
import { renderSignedIn } from "@/test/renderSignedIn";

async function renderApp() {
  await renderSignedIn(
    <Routes>
      <Route path="/app" element={<PhoneFrame />}>
        <Route path="pay" element={<PayPage />} />
        <Route path="pay/:txnId/coach" element={<ScamCoachPage />} />
        <Route path="pay/:txnId/warning" element={<RiskWarningPage />} />
        <Route path="pay/:txnId/done" element={<PaymentDonePage />} />
        <Route path="settings" element={<SettingsPage />} />
      </Route>
    </Routes>,
    "customer@demo.fraudshield",
    "/app/settings",
  );
}

/** Settings → "Scam call", then pay (spec 07 §5 digital-arrest demo). */
async function payDuringScamCall(vpa: string, amount: string) {
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "Scam call" }));
  await user.click(screen.getByRole("link", { name: "Pay" }));
  await user.type(await screen.findByLabelText("Pay to (UPI ID)"), vpa);
  await user.type(screen.getByLabelText("Amount"), amount);
  await user.click(screen.getByRole("button", { name: "Pay" }));
  return user;
}

describe("Scam Coach (spec 07 §3, §5; T5.4)", () => {
  it("digital arrest: the coach asks first, one YES stops the payment with 'Call my bank'", async () => {
    const sent: CoachRequest[] = [];
    server.events.on("request:start", async ({ request }) => {
      if (request.url.endsWith("/coach")) sent.push((await request.clone().json()) as CoachRequest);
    });
    await renderApp();
    const user = await payDuringScamCall("refund.help@okbank", "48500");

    expect(await screen.findByRole("heading", { name: "Wait. Answer 3 quick questions first" })).toBeInTheDocument();
    expect(screen.getByRole("timer")).toHaveTextContent(/Cooling-off time: (10:00|9:5\d) left/);
    const check = screen.getByRole("button", { name: "Check my answers" });
    expect(check).toBeDisabled(); // every question first
    // the biggest button is still Cancel (spec 07 §4)
    expect(screen.getByRole("button", { name: "Cancel payment" })).toBeInTheDocument();

    const groups = screen.getAllByRole("radiogroup");
    expect(groups[0]).toHaveAccessibleName("1. Is someone on a call telling you to make this payment?");
    await user.click(screen.getAllByRole("radio", { name: "Yes" })[0] as HTMLElement);
    for (const no of screen.getAllByRole("radio", { name: "No" }).slice(1)) await user.click(no);
    await user.click(check);

    expect(await screen.findByRole("heading", { name: "We stopped this payment to protect you" })).toBeInTheDocument();
    expect(screen.getByText(/Hang up now\. Anyone telling you on a call to send money is a scammer/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Call my bank (1930)" })).toHaveAttribute("href", "tel:1930");
    expect(screen.getByText("Warning signs in your answers: 1")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Pay anyway/ })).not.toBeInTheDocument(); // no way on
    expect(sent).toEqual([{ answers: { on_call_instructed: true, threat_or_arrest: false, asked_secrecy: false } }]);
  });

  it("safe answers on an extra check still wait for the cooling-off time", async () => {
    server.use(
      http.post(`${API_URL}/transactions/score`, async ({ request }) => {
        const body = (await request.json()) as ScoreRequest;
        const response: ScoreResponse = {
          txn_id: body.txn_id, risk_score: 0.1, decision: "STEP_UP", action: "SCAM_COACH",
          model_scores: { transaction: 0.1, anomaly: 0.1, graph: 0, social_eng: 0.6 },
          top_reasons: [], explanation: "Before you pay, please answer a few quick questions.",
          explanation_status: "pending",
          coach_questions: [
            { key: "on_call_instructed", text: "Is someone on a call telling you to make this payment?" },
            { key: "know_payee", text: "Have you met or paid this person before?" },
          ],
          model_version: "test", degraded: [], latency_ms: 9,
        };
        return HttpResponse.json(response);
      }),
      http.post(`${API_URL}/transactions/:txnId/coach`, ({ params }) => {
        const response: CoachResponse = {
          txn_id: String(params.txnId), risk_score: 0.1, decision: "STEP_UP", action: "OTP",
          explanation: "Please confirm this payment with the OTP we sent you.", explanation_status: "pending",
          top_reasons: [], red_flags: 0,
        };
        return HttpResponse.json(response);
      }),
    );
    await renderApp();
    const user = await payDuringScamCall("anita.k@paytm", "900");

    expect(await screen.findByRole("heading", { name: "Wait. Answer 2 quick questions first" })).toBeInTheDocument();
    await user.click(screen.getAllByRole("radio", { name: "No" })[0] as HTMLElement); // nobody on a call
    await user.click(screen.getAllByRole("radio", { name: "Yes" })[1] as HTMLElement); // paid them before
    await user.click(screen.getByRole("button", { name: "Check my answers" }));

    expect(await screen.findByRole("heading", { name: "Thank you. Your answers look safe" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "You can pay after the cooling-off time" })).toBeDisabled();
    expect(screen.getByRole("link", { name: "Call my bank (1930)" })).toBeInTheDocument();
  });
});
