import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AuditPage } from "@/dashboard/pages/AuditPage";
import { ModelsPage } from "@/dashboard/pages/ModelsPage";
import { PayeesPage } from "@/dashboard/pages/PayeesPage";
import { RulesPage } from "@/dashboard/pages/RulesPage";
import { mockRules } from "@/mocks/handlers";
import { server } from "@/mocks/node";
import { API_URL } from "@/shared/api/client";
import { renderSignedIn } from "@/test/renderSignedIn";

const ADMIN = "admin@demo.fraudshield";

describe("Rules Manager (spec 06 §3, §4)", () => {
  it("rejects an invalid rule before submit and sends nothing", async () => {
    let posts = 0;
    server.events.on("request:start", ({ request }) => { if (request.method === "POST" && request.url.endsWith("/rules")) posts += 1; });
    await renderSignedIn(<RulesPage />, ADMIN);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "New rule" }));
    await user.selectOptions(screen.getByLabelText("Feature, condition 1"), "payee_is_new");
    // a yes/no feature only offers == and !=
    expect(within(screen.getByLabelText("Operator, condition 1")).getAllByRole("option").map((o) => o.textContent)).toEqual(["==", "!="]);
    await user.click(screen.getByRole("button", { name: "Save rule" }));

    expect(screen.getByText("Give the rule a name.")).toBeInTheDocument();
    expect(screen.getByText("Choose true or false.")).toBeInTheDocument();
    expect(screen.getByText("Fix the highlighted fields. Nothing was sent.")).toBeInTheDocument();
    expect(posts).toBe(0);
    server.events.removeAllListeners();
  });

  it("creates a valid rule with a live JSON preview, then lists it", async () => {
    await renderSignedIn(<RulesPage />, ADMIN);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "New rule" }));
    await user.type(screen.getByLabelText("Name"), "Fuel at night");
    await user.selectOptions(screen.getByLabelText("Feature, condition 1"), "merchant_category");
    await user.selectOptions(screen.getByLabelText("Value, condition 1"), "FUEL");
    await user.click(screen.getByRole("button", { name: "Add condition" }));
    await user.selectOptions(screen.getByLabelText("Feature, condition 2"), "is_night");
    await user.selectOptions(screen.getByLabelText("Value, condition 2"), "true");
    await user.click(screen.getByRole("radio", { name: "Held" }));
    expect(screen.getByLabelText("JSON sent to the API")).toHaveTextContent('"field": "is_night", "op": "==", "value": true');

    await user.click(screen.getByRole("button", { name: "Save rule" }));
    expect(await screen.findByText("Rule created")).toBeInTheDocument();
    expect(await screen.findByText("merchant_category == FUEL AND is_night == true")).toBeInTheDocument();
    expect(mockRules().find((r) => r.name === "Fuel at night")).toMatchObject({ action: "HOLD", is_active: true });
  });

  it("shows the API's answer when the name is taken", async () => {
    await renderSignedIn(<RulesPage />, ADMIN);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "New rule" }));
    await user.type(screen.getByLabelText("Name"), "Reported payee");
    await user.selectOptions(screen.getByLabelText("Feature, condition 1"), "payee_report_count");
    await user.type(screen.getByLabelText("Value, condition 1"), "1");
    await user.click(screen.getByRole("button", { name: "Save rule" }));
    expect(await screen.findByText("A rule with this name already exists")).toBeInTheDocument();
  });

  it("turns a rule off and deletes one after confirming", async () => {
    await renderSignedIn(<RulesPage />, ADMIN);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("switch", { name: "Rule “Night-time burst” active" }));
    expect(await screen.findByText("Rule turned off")).toBeInTheDocument();
    expect(mockRules().find((r) => r.name === "Night-time burst")?.is_active).toBe(false);

    await user.click(screen.getByRole("button", { name: "Delete rule “Rooted phone, big amount”" }));
    await user.click(screen.getByRole("button", { name: "Delete" }));
    expect(await screen.findByText("Rule deleted")).toBeInTheDocument();
    expect(mockRules().some((r) => r.name === "Rooted phone, big amount")).toBe(false);
  });
});

describe("Model Monitor", () => {
  it("shows the active model's metrics and honest drift status; deploying an unloaded one explains why not", async () => {
    await renderSignedIn(<ModelsPage />, ADMIN);
    const user = userEvent.setup();
    expect(await screen.findByText("ensemble-2026-10-18-txn2")).toBeInTheDocument();
    expect(screen.getByText("0.997")).toBeInTheDocument(); // PR-AUC
    expect(screen.getAllByText(/Drift: not checked yet/)).toHaveLength(2);
    expect(screen.getByText("Serving every payment")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Deploy" }));
    await user.click(screen.getByRole("button", { name: "Deploy" }));
    expect(await screen.findByText("Not deployed")).toBeInTheDocument();
    expect(screen.getByText(/Only the loaded model can be deployed/)).toBeInTheDocument();
  });
});

describe("Audit Log", () => {
  afterEach(() => vi.restoreAllMocks());

  it("filters, opens a row's before/after and exports CSV with the same filters", async () => {
    const createUrl = vi.fn(() => "blob:csv");
    Object.assign(URL, { createObjectURL: createUrl, revokeObjectURL: vi.fn() });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    let csvQuery = "";
    server.events.on("request:start", ({ request }) => { if (request.url.includes("format=csv")) csvQuery = new URL(request.url).search; });
    await renderSignedIn(<AuditPage />, ADMIN);
    const user = userEvent.setup();
    expect(await screen.findByText("case.update")).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("Action"), "case.update");
    expect(await screen.findByText("1–1 of 1")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Show before and after/ }));
    expect(screen.getByText(/"label": "fraud"/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Export CSV" }));
    await vi.waitFor(() => expect(click).toHaveBeenCalled());
    expect(csvQuery).toContain("action=case.update");
    expect(csvQuery).not.toContain("page");
    expect(createUrl).toHaveBeenCalled();
    server.events.removeAllListeners();
  });

  it("an admin's own actions can be picked out", async () => {
    await renderSignedIn(<AuditPage />, ADMIN);
    const user = userEvent.setup();
    await screen.findByText("case.update");
    await user.click(screen.getByRole("checkbox", { name: "Only my actions" }));
    const bodyRows = () => screen.getAllByRole("row").filter((r) => within(r).queryAllByRole("cell").length > 0);
    await vi.waitFor(() => {
      const rows = bodyRows();
      expect(rows.length).toBeGreaterThan(0);
      for (const row of rows) expect(within(row).getByText("You")).toBeInTheDocument(); // every entry is mine
    });
    expect(bodyRows().some((r) => within(r).queryByText("case.update"))).toBe(false); // the analyst's is gone
  });
});

describe("Payee Reputation", () => {
  it("looks up a UPI ID and shows reports by type and the latest reports", async () => {
    await renderSignedIn(<PayeesPage />);
    const user = userEvent.setup();
    await user.type(screen.getByRole("textbox"), "refund.helpdesk@ybl{Enter}");
    expect(await screen.findByText("refund.helpdesk@ybl")).toBeInTheDocument();
    expect(screen.getByText("Reports by scam type")).toBeInTheDocument();
    expect(screen.getByText("Caller 98xxxxxx21")).toBeInTheDocument();
    expect(screen.getAllByText("Fake bank or refund help").length).toBeGreaterThan(0);
  });

  it("checks the UPI ID and says when nobody knows the payee", async () => {
    server.use(http.get(`${API_URL}/payees/:vpa/reputation`, ({ params }) =>
      HttpResponse.json({ vpa: params.vpa, known: false, reputation_score: 0, risk_level: "low", report_count: 0, confirmed_fraud_count: 0,
        graph_flag: false, account_age_days: null, reports_by_type: {}, recent_reports: [] })));
    await renderSignedIn(<PayeesPage />);
    const user = userEvent.setup();
    await user.type(screen.getByRole("textbox"), "not a vpa{Enter}");
    expect(screen.getByText("Enter a UPI ID like name@bank")).toBeInTheDocument();
    await user.clear(screen.getByRole("textbox"));
    await user.type(screen.getByRole("textbox"), "nobody.knows@ybl{Enter}");
    expect(await screen.findByText("No record of nobody.knows@ybl")).toBeInTheDocument();
  });
});
