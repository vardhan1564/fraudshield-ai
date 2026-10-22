import { afterEach, describe, expect, it, vi } from "vitest";

import { deviceId, newTxnId, randomHex } from "@/customer/lib/device";

describe("device ids", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("work on a plain-http page, where crypto.randomUUID does not exist", () => {
    vi.stubGlobal("crypto", { getRandomValues: crypto.getRandomValues.bind(crypto) });
    expect(randomHex(8)).toMatch(/^[0-9a-f]{8}$/);
    expect(newTxnId()).toMatch(/^WEB-[0-9A-Z]+-[0-9A-F]{6}$/);
    expect(deviceId()).toMatch(/^D-web-[0-9a-f]{8}$/);
  });

  it("keeps one device id per browser", () => {
    expect(deviceId()).toBe(deviceId());
  });
});
