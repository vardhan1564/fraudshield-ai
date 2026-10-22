import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ShapWaterfall } from "@/dashboard/components/ShapWaterfall";
import type { Reason } from "@/shared/types/api";

const SHAP: Reason[] = [
  { feature: "device_age_days", value: 412, impact: -0.42, text: "Known device used for over a year" },
  { feature: "payee_report_count", value: 7, impact: 1.2, text: "Payee reported by 7 users" },
  { feature: "ctx_screen_share", value: true, impact: 0.8, text: "Screen sharing is on" },
];

describe("ShapWaterfall", () => {
  it("names both directions in a legend, so colour is never the only cue", () => {
    render(<ShapWaterfall shap={SHAP} />);
    expect(screen.getByText("What drove the score")).toBeInTheDocument();
    expect(screen.getByText("Raises risk")).toBeInTheDocument();
    expect(screen.getByText("Lowers risk")).toBeInTheDocument();
  });

  it("has a table view: biggest push first, signed values, running total, net effect", async () => {
    render(<ShapWaterfall shap={SHAP} />);
    await userEvent.setup().click(screen.getByRole("button", { name: "Table" }));
    const rows = screen.getAllByRole("row").slice(1).map((r) => within(r).getAllByRole("cell").map((c) => c.textContent));
    expect(rows).toEqual([
      ["Payee reported by 7 users", "7", "+1.20", "+1.20"],
      ["Screen sharing is on", "Yes", "+0.80", "+2.00"],
      ["Known device used for over a year", "412", "−0.42", "+1.58"],
      ["Net effect of these 10", "", "+1.58", ""],
    ]);
  });

  it("says so when there is nothing to explain", () => {
    render(<ShapWaterfall shap={[]} />);
    expect(screen.getByText("No model explanation for this payment.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Chart" })).not.toBeInTheDocument();
  });
});
