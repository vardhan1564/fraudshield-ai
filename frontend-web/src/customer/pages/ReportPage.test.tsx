import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { PhoneFrame } from "@/customer/layout/PhoneFrame";
import { HomePage } from "@/customer/pages/HomePage";
import { PayPage } from "@/customer/pages/PayPage";
import { ReportPage } from "@/customer/pages/ReportPage";
import { server } from "@/mocks/node";
import { API_URL } from "@/shared/api/client";
import type { ReportCreate } from "@/shared/types/api";
import { renderSignedIn } from "@/test/renderSignedIn";

async function renderApp(path = "/app/report") {
  await renderSignedIn(
    <Routes>
      <Route path="/app" element={<PhoneFrame />}>
        <Route index element={<HomePage />} />
        <Route path="pay" element={<PayPage />} />
        <Route path="report" element={<ReportPage />} />
      </Route>
    </Routes>,
    "customer@demo.fraudshield",
    path,
  );
}

/** Each test reports its own payee: the mock API remembers reports for the whole file. */
async function report(vpa: string, type: string, extra: { phone?: string; what?: string } = {}) {
  const user = userEvent.setup();
  await user.type(await screen.findByLabelText("Their UPI ID"), vpa);
  if (type) await user.click(screen.getByRole("radio", { name: type }));
  if (extra.phone) await user.type(screen.getByLabelText(/Number that called you/), extra.phone);
  if (extra.what) await user.type(screen.getByLabelText(/What happened/), extra.what);
  await user.click(screen.getByRole("button", { name: "Send report" }));
  return user;
}

describe("Report Fraud (spec 07 §3)", () => {
  it("sends the report and thanks the customer with the payee's new report count", async () => {
    const sent: ReportCreate[] = [];
    server.events.on("request:start", async ({ request }) => {
      if (request.method === "POST" && request.url.endsWith("/reports")) sent.push((await request.clone().json()) as ReportCreate);
    });
    await renderApp();
    await report("Refund.Helpline@YBL", "Fake bank or refund help", { phone: "+91 98123 45621", what: "Asked me to pay to get a refund" });

    expect(await screen.findByRole("heading", { name: "Thank you for reporting" })).toBeInTheDocument();
    expect(screen.getByText("9 people have now reported re***@ybl.")).toBeInTheDocument(); // demo scam payee: 8 + this one
    expect(sent[0]).toEqual({
      payee_vpa: "refund.helpline@ybl",
      scam_type: "fake_support_refund",
      phone: "9812345621",
      description: "Asked me to pay to get a refund",
    });
    server.events.removeAllListeners();
  });

  it("the Pay screen then shows the higher count for that payee", async () => {
    await renderApp();
    const user = await report("new.cheat@okaxis", "Job or online task");
    expect(await screen.findByText("1 person has now reported ne***@okaxis.")).toBeInTheDocument();

    await user.click(screen.getByRole("link", { name: "Pay" }));
    await user.type(await screen.findByLabelText("Pay to (UPI ID)"), "new.cheat@okaxis");
    await user.tab();
    expect(await screen.findByText("1 person reported this payee. Be careful.")).toBeInTheDocument();
  });

  it("a second report of the same payee is thanked, not shown as an error", async () => {
    await renderApp();
    const user = await report("twice.scam@paytm", "Lottery or prize");
    await screen.findByRole("heading", { name: "Thank you for reporting" });
    await user.click(screen.getByRole("button", { name: "Report another scam" }));
    await report("twice.scam@paytm", "Lottery or prize");

    expect(await screen.findByText("You have already reported this payee. Thank you.")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("asks for a UPI ID, the kind of scam and a real mobile number, in plain words", async () => {
    let calls = 0;
    server.use(http.post(`${API_URL}/reports`, () => { calls += 1; return HttpResponse.json({}, { status: 500 }); }));
    await renderApp();
    await report("nope", "", { phone: "12345" });

    expect(screen.getByText("Enter a UPI ID like name@bank")).toBeInTheDocument();
    expect(screen.getByText("Choose the kind of scam")).toBeInTheDocument();
    expect(screen.getByText("Enter a 10-digit mobile number")).toBeInTheDocument();
    expect(calls).toBe(0);
  });
});
