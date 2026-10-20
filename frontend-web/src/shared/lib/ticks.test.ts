import { describe, expect, it } from "vitest";

import { niceTicks } from "@/shared/lib/ticks";

describe("niceTicks", () => {
  it("gives 3 evenly spaced round ticks that cover the max snugly", () => {
    expect(niceTicks(780)).toEqual([0, 400, 800]);
    expect(niceTicks(57)).toEqual([0, 30, 60]);
    expect(niceTicks(19)).toEqual([0, 10, 20]);
    expect(niceTicks(3)).toEqual([0, 2, 4]);
    expect(niceTicks(12_480)).toEqual([0, 8_000, 16_000]);
  });

  it("always covers the max", () => {
    for (const max of [1, 7, 99, 101, 640, 1_999, 25_001]) {
      const [, , top] = niceTicks(max);
      expect(top).toBeGreaterThanOrEqual(max);
      expect(top).toBeLessThan(max * 2.5);
    }
  });

  it("handles empty data", () => {
    expect(niceTicks(0)).toEqual([0, 1, 2]);
  });
});
