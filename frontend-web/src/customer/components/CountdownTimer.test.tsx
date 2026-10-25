import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CountdownTimer } from "@/customer/components/CountdownTimer";
import { COOLING_OFF_MS, flaggedKeys, formatRemaining } from "@/customer/lib/coach";

describe("CountdownTimer (spec 07 §6)", () => {
  afterEach(() => vi.useRealTimers());

  it("counts the cooling-off time down and then says it is over", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-25T10:00:00Z"));
    const endsAt = Date.now() + COOLING_OFF_MS;
    render(<CountdownTimer endsAt={endsAt} running={(left) => `${left} left`} done="Over" />);
    expect(screen.getByRole("timer")).toHaveTextContent("10:00 left");

    act(() => vi.advanceTimersByTime(61_000));
    expect(screen.getByRole("timer")).toHaveTextContent("8:59 left");

    act(() => vi.advanceTimersByTime(COOLING_OFF_MS));
    expect(screen.getByRole("timer")).toHaveTextContent("Over");
  });

  it("formats what is left as m:ss, never negative", () => {
    expect(formatRemaining(600_000)).toBe("10:00");
    expect(formatRemaining(65_400)).toBe("1:06"); // rounds up: never shows 0:00 too early
    expect(formatRemaining(-5_000)).toBe("0:00");
  });
});

describe("flaggedKeys", () => {
  it("finds the warning-sign answers (YES, but NO for 'have you paid them before?')", () => {
    const qs = ["on_call_instructed", "refund_to_receive", "know_payee"].map((key) => ({ key, text: key }));
    expect(flaggedKeys(qs, { on_call_instructed: false, refund_to_receive: false, know_payee: true })).toEqual([]);
    expect(flaggedKeys(qs, { on_call_instructed: true, refund_to_receive: false, know_payee: false })).toEqual([
      "on_call_instructed",
      "know_payee",
    ]);
  });
});
