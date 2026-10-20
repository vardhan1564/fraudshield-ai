import { render, screen } from "@testing-library/react";
import { Gauge, Wallet } from "lucide-react";
import { describe, expect, it } from "vitest";

import { KpiCard } from "@/dashboard/components/KpiCard";

describe("KpiCard", () => {
  it("shows the label, the formatted value with its full value, and the hint", () => {
    render(<KpiCard label="Money protected" value="₹38.45L" fullValue="₹38,45,200" hint="Held payments" icon={Wallet} />);
    expect(screen.getByText("Money protected")).toBeInTheDocument();
    const value = screen.getByText("₹38.45L");
    expect(value).toHaveAttribute("title", "₹38,45,200");
    expect(screen.getByText("Held payments")).toBeInTheDocument();
  });

  it("shows a dash when there is no value", () => {
    render(<KpiCard label="False-positive rate" value={null} hint="No labelled cases yet" icon={Gauge} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("states a status in words with an icon (not colour alone), replacing the hint", () => {
    const { container } = render(
      <KpiCard
        label="p95 response time"
        value="134 ms"
        hint="ignored when a status is shown"
        icon={Gauge}
        status={{ tone: "critical", label: "Over the 100 ms budget" }}
      />,
    );
    const status = screen.getByText("Over the 100 ms budget");
    expect(status).toHaveClass("text-risk-high");
    expect(status.querySelector("svg")).not.toBeNull();
    expect(container).not.toHaveTextContent("ignored when a status is shown");
  });

  it("uses the good tone within budget", () => {
    render(<KpiCard label="p95" value="34 ms" icon={Gauge} status={{ tone: "good", label: "Within the 100 ms budget" }} />);
    expect(screen.getByText("Within the 100 ms budget")).toHaveClass("text-risk-low");
  });

  it("renders a same-shape skeleton while loading, without the value", () => {
    render(<KpiCard label="Payments checked" value="12,480" icon={Gauge} loading />);
    expect(screen.getByTestId("kpi-skeleton")).toHaveAttribute("aria-busy", "true");
    expect(screen.queryByText("12,480")).toBeNull();
  });
});
