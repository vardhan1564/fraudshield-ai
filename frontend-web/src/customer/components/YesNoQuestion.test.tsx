import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { YesNoQuestion } from "@/customer/components/YesNoQuestion";

describe("YesNoQuestion (spec 07 §6)", () => {
  it("is a radio group named by the question, with labelled Yes / No", async () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <YesNoQuestion id="q1" number={1} question="Is someone on a call?" value={null} onChange={onChange} />,
    );
    const group = screen.getByRole("radiogroup", { name: "1. Is someone on a call?" });
    expect(group).toBeInTheDocument();
    const yes = screen.getByRole("radio", { name: "Yes" });
    const no = screen.getByRole("radio", { name: "No" });
    expect(yes).toHaveAttribute("aria-checked", "false");
    expect(no).toHaveAttribute("aria-checked", "false");

    await userEvent.click(yes);
    expect(onChange).toHaveBeenCalledWith(true);
    rerender(<YesNoQuestion id="q1" number={1} question="Is someone on a call?" value={true} onChange={onChange} />);
    expect(screen.getByRole("radio", { name: "Yes" })).toHaveAttribute("aria-checked", "true");
    await userEvent.click(screen.getByRole("radio", { name: "No" }));
    expect(onChange).toHaveBeenLastCalledWith(false);
  });

  it("can't be changed while the answers are being checked", async () => {
    const onChange = vi.fn();
    render(<YesNoQuestion id="q2" number={2} question="Q" value={null} onChange={onChange} disabled />);
    await userEvent.click(screen.getByRole("radio", { name: "Yes" }));
    expect(onChange).not.toHaveBeenCalled();
  });
});
