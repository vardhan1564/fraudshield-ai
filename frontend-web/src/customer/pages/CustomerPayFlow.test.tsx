import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { PhoneFrame } from "@/customer/layout/PhoneFrame";
import { HomePage } from "@/customer/pages/HomePage";
import { PayPage } from "@/customer/pages/PayPage";
import { PaymentDonePage } from "@/customer/pages/PaymentDonePage";
import { ReportPage } from "@/customer/pages/ReportPage";
import { RiskWarningPage } from "@/customer/pages/RiskWarningPage";
import { SettingsPage } from "@/customer/pages/SettingsPage";
import { server } from "@/mocks/node";
import { API_URL } from "@/shared/api/client";
import type { ScoreRequest, ScoreResponse } from "@/shared/types/api";
import { controlCustomerSocket, renderSignedIn } from "@/test/renderSignedIn";

const CUSTOMER = "customer@demo.fraudshield";

async function renderApp(path: string) {
  await renderSignedIn(
    <Routes>
      <Route path="/app" element={<PhoneFrame />}>
        <Route index element={<HomePage />} />
        <Route path="pay" element={<PayPage />} />
        <Route path="pay/:txnId/warning" element={<RiskWarningPage />} />
        <Route path="pay/:txnId/done" element={<PaymentDonePage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="report" element={<ReportPage />} />
      </Route>
    </Routes>,
    CUSTOMER,
    path,
  );
}

async function pay(vpa: string, amount: string) {
  const user = userEvent.setup();
  await user.type(await screen.findByLabelText("Pay to (UPI ID)"), vpa);
  await user.type(screen.getByLabelText("Amount"), amount); // leaving the UPI field checks the payee
  await user.click(screen.getByRole("button", { name: "Pay" }));
  return user;
}

/** Answer /transactions/score with this decision and keep the request for checks. */
function scoreAs(decision: ScoreResponse["decision"], sent: ScoreRequest[] = []) {
  server.use(
    http.post(`${API_URL}/transactions/score`, async ({ request }) => {
      const body = (await request.json()) as ScoreRequest;
      sent.push(body);
      const response: ScoreResponse = {
        txn_id: body.txn_id, risk_score: decision === "HOLD" ? 0.9 : 0.05, decision,
        action: decision === "HOLD" ? "ANALYST_REVIEW" : decision === "STEP_UP" ? "OTP" : "NONE",
        model_scores: { transaction: 0.1, anomaly: 0.1, graph: 0, social_eng: 0.1 },
        top_reasons: decision === "APPROVE" ? [] : [{ feature: "payee_is_new", value: true, impact: 0.6, text: "First payment to this payee" }],
        explanation: decision === "APPROVE" ? "This payment looks normal." : "Please check before you pay.",
        explanation_status: decision === "APPROVE" ? "ready" : "pending",
        coach_questions: [], model_version: "test", degraded: [], latency_ms: 9,
      };
      return HttpResponse.json(response);
    }),
  );
  return sent;
}

describe("Customer app: Pay (spec 07 §3, §5)", () => {
  it("a normal ₹200 payment goes straight to 'Payment sent'", async () => {
    const sent = scoreAs("APPROVE");
    await renderApp("/app/pay");
    await pay("grocer.mart@okhdfcbank", "200");

    expect(await screen.findByRole("heading", { name: "Payment sent" })).toBeInTheDocument();
    expect(screen.getByText("₹200")).toBeInTheDocument();
    expect(sent[0]).toMatchObject({ amount: 200, payee_vpa: "grocer.mart@okhdfcbank", channel: "UPI", context: { on_call: false } });
    expect(sent[0]?.device_id).toBe(`D-phone-${sent[0]?.user_id}`); // the usual, registered phone
  });

  it("with 'my usual phone' off, pays as this browser's own new phone", async () => {
    const sent = scoreAs("APPROVE");
    await renderApp("/app/settings");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("switch", { name: /Using my usual phone/ }));
    await user.click(screen.getByRole("link", { name: "Pay" }));
    expect(await screen.findByText(/new phone/)).toBeInTheDocument(); // the Simulated banner says so
    await pay("grocer.mart@okhdfcbank", "200");
    await screen.findByRole("heading", { name: "Payment sent" });
    expect(sent[0]?.device_id).toMatch(/^D-web-[0-9a-f]{8}$/);
  });

  it("shows the payee's reports before paying", async () => {
    await renderApp("/app/pay");
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Pay to (UPI ID)"), "refund.help@okbank");
    await user.tab();
    expect(await screen.findByText("Reported as a scam by 8 people")).toBeInTheDocument();
  });

  it("checks the UPI ID and amount in plain words, without calling the API", async () => {
    const sent = scoreAs("APPROVE");
    await renderApp("/app/pay");
    await pay("not a vpa", "0");
    expect(screen.getByText("Enter a UPI ID like name@bank")).toBeInTheDocument();
    expect(screen.getByText("Enter an amount, like 200")).toBeInTheDocument();
    expect(sent).toHaveLength(0);
  });

  it("sends the simulated scam call and shows the red paused screen with Cancel biggest", async () => {
    const sent = scoreAs("HOLD");
    await renderApp("/app/settings");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Scam call" }));
    expect(screen.getByRole("switch", { name: "On a phone call" })).toHaveAttribute("aria-checked", "true");
    await user.click(screen.getByRole("link", { name: "Pay" }));
    await pay("cbi.verification@okaxis", "48500");

    const alert = await screen.findByRole("alert");
    expect(within(alert).getByRole("heading", { name: "We have paused this payment" })).toBeInTheDocument();
    expect(screen.getByText("First payment to this payee")).toBeInTheDocument();
    expect(screen.queryByText(/0\.9/)).not.toBeInTheDocument(); // never a score
    expect(screen.getByRole("link", { name: "Call cyber-fraud helpline 1930" })).toHaveAttribute("href", "tel:1930");
    expect(screen.queryByRole("button", { name: /Pay anyway/ })).not.toBeInTheDocument();
    expect(sent[0]?.context).toMatchObject({ on_call: true, caller_in_contacts: false, screen_share_active: true, remote_access_app: "AnyDesk" });
  });
});

describe("Customer app: Risk Warning", () => {
  it("replaces the explanation when the worker's sentence for this payment arrives", async () => {
    const sent = scoreAs("STEP_UP");
    const socket = controlCustomerSocket();
    await renderApp("/app/pay");
    await pay("new.shop@ybl", "6000");
    await screen.findByRole("heading", { name: "Stop. This payment may be a scam" });
    await socket.connected;
    expect(screen.getByText("Please check before you pay.")).toBeInTheDocument();
    expect(screen.getByText("Getting more detail…")).toBeInTheDocument();

    socket.push({ type: "explanation.ready", data: { txn_id: "SOMEONE-ELSE", explanation: "Not yours" } });
    socket.push({ type: "explanation.ready", data: { txn_id: sent[0]?.txn_id, explanation: "The payee's account opened yesterday." } });
    expect(await screen.findByText("The payee's account opened yesterday.")).toBeInTheDocument();
    expect(screen.queryByText("Not yours")).not.toBeInTheDocument();
    expect(screen.queryByText("Getting more detail…")).not.toBeInTheDocument();
  });

  it("extra check: Cancel is the big button; 'pay anyway' leads to sent with a reminder", async () => {
    scoreAs("STEP_UP");
    await renderApp("/app/pay");
    const user = await pay("new.shop@ybl", "6000");
    await screen.findByRole("heading", { name: "Stop. This payment may be a scam" });
    const cancel = screen.getByRole("button", { name: "Cancel payment" });
    expect(cancel.className).toContain("h-16");

    await user.click(screen.getByRole("button", { name: "I know this person. Pay anyway" }));
    expect(await screen.findByRole("heading", { name: "Payment sent" })).toBeInTheDocument();
    expect(screen.getByText(/You chose to pay after a warning/)).toBeInTheDocument();
  });

  it("'Report this payee' opens the report form with the UPI ID filled in", async () => {
    scoreAs("HOLD");
    await renderApp("/app/pay");
    const user = await pay("cbi.verification@okaxis", "48500");
    await user.click(await screen.findByRole("link", { name: "Report this payee as a scam" }));
    expect(await screen.findByRole("heading", { name: "Report a scam" })).toBeInTheDocument();
    expect(screen.getByLabelText("Their UPI ID")).toHaveValue("cbi.verification@okaxis");
  });

  it("Cancel payment goes home and says the money is safe", async () => {
    scoreAs("HOLD");
    await renderApp("/app/pay");
    const user = await pay("cbi.verification@okaxis", "48500");
    await user.click(await screen.findByRole("button", { name: "Cancel payment" }));
    expect(await screen.findByText("Your money is safe.")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: /Hello/ })).toBeInTheDocument();
  });
});
