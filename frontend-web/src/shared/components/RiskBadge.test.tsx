import { act, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RiskBadge } from "@/shared/components/RiskBadge";
import { setLanguage } from "@/shared/i18n";

describe("RiskBadge", () => {
  it("names the level in words next to the score (not colour alone)", () => {
    render(<RiskBadge score={0.91} />);
    expect(screen.getByText("High")).toBeInTheDocument();
    expect(screen.getByText("0.91")).toBeInTheDocument();
  });

  it("hides the score for customers", () => {
    render(<RiskBadge score={0.05} showScore={false} />);
    expect(screen.getByText("Low")).toBeInTheDocument();
    expect(screen.queryByText("0.05")).not.toBeInTheDocument();
  });

  it("speaks the analyst's language", () => {
    render(<RiskBadge score={0.91} />);
    act(() => setLanguage("hi"));
    expect(screen.getByText("उच्च")).toBeInTheDocument();
  });
});
