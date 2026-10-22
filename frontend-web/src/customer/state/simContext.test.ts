import { describe, expect, it } from "vitest";

import { isActive, loadSim, NORMAL, SCAM_CALL, saveSim, toPaymentContext } from "@/customer/state/simContext";

describe("simulated phone context", () => {
  it("maps the panel to the score request's context (spec 04 §2.1)", () => {
    expect(toPaymentContext(SCAM_CALL)).toEqual({
      on_call: true,
      caller_in_contacts: false,
      call_duration_s: 2400,
      screen_share_active: true,
      remote_access_app: "AnyDesk",
      is_vpn: false,
    });
    expect(toPaymentContext(NORMAL)).toMatchObject({ on_call: false, caller_in_contacts: false, call_duration_s: 0 });
    // a known caller only counts while there is a call
    expect(toPaymentContext({ ...NORMAL, onCall: true })).toMatchObject({ on_call: true, caller_in_contacts: true });
  });

  it("is remembered, and bad saved data falls back to normal", () => {
    saveSim(SCAM_CALL);
    expect(loadSim()).toEqual(SCAM_CALL);
    window.localStorage.setItem("fraudshield.simContext", "{not json");
    expect(loadSim()).toEqual(NORMAL);
    window.localStorage.setItem("fraudshield.simContext", JSON.stringify({ onCall: "yes", remoteApp: "EvilApp", callMinutes: 999 }));
    expect(loadSim()).toEqual(NORMAL);
  });

  it("knows when any signal is on", () => {
    expect(isActive(NORMAL)).toBe(false);
    expect(isActive({ ...NORMAL, vpn: true })).toBe(true);
    expect(isActive(SCAM_CALL)).toBe(true);
  });
});
