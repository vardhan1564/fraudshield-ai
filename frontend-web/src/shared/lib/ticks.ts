const STEPS = [1, 2, 2.5, 3, 4, 5, 6, 8, 10];

/**
 * Three clean, evenly spaced y ticks for a chart from 0: [0, step, 2 × step], where step is the
 * smallest 1/2/2.5/3/4/5/6/8 × 10^n that fits half the max (no ragged 200/400/800 ladders).
 */
export function niceTicks(max: number): [number, number, number] {
  if (!(max > 0)) return [0, 1, 2];
  const half = max / 2;
  const pow = 10 ** Math.floor(Math.log10(half));
  const step = (STEPS.find((m) => m * pow >= half) ?? 10) * pow;
  return [0, step, step * 2];
}
