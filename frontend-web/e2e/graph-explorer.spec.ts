import { expect, type Page, test } from "@playwright/test";

// T5.3 done-when, on the demo build (the `__graph` handle exists only in mock mode):
// 200 nodes render smoothly, a suspected ring is highlighted, clicking a node loads its neighbourhood.
const EMAIL = "analyst@demo.fraudshield";
const PASSWORD = "demo-pass-123";

interface GraphHandle {
  nodeCount: () => number;
  ringNodeCount: () => number;
  renderedPosition: (id: string) => { x: number; y: number } | null;
}

type Win = Window & { __graph?: GraphHandle };

const nodeCount = (page: Page) => page.evaluate(() => (window as Win).__graph?.nodeCount() ?? 0);
const ringNodeCount = (page: Page) => page.evaluate(() => (window as Win).__graph?.ringNodeCount() ?? 0);
const positionOf = (page: Page, id: string) =>
  page.evaluate((nodeId) => (window as Win).__graph?.renderedPosition(nodeId) ?? null, id);

test.skip(!!process.env.E2E_BASE_URL, "uses the mock-only graph handle");

test("graph explorer: 200 nodes render smoothly, the ring is highlighted, a click expands a node", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/dashboard$/);

  await page.getByRole("link", { name: "Graph explorer" }).click();
  await page.getByRole("button", { name: "Open ring of 9 accounts, score 0.94" }).click();
  await expect(page).toHaveURL(/\/dashboard\/graph\/A%3AU0901$/);

  // a suspected ring is highlighted: all 9 members carry the ring class
  await expect.poll(() => ringNodeCount(page)).toBe(9);

  // 200 nodes: depth 3 reaches the API's node cap
  const started = Date.now();
  await page.getByRole("radio", { name: "3 hops" }).click();
  await expect.poll(() => nodeCount(page), { timeout: 10_000 }).toBe(200);
  const renderMs = Date.now() - started;
  await expect(page.getByText("Only the first 200 nodes are shown.", { exact: false })).toBeVisible();

  // smooth: frame times while panning and zooming the 200-node graph
  const canvas = page.getByTestId("graph-canvas");
  const box = await canvas.boundingBox();
  if (!box) throw new Error("no graph canvas");
  await page.evaluate(() => {
    const w = window as unknown as { __frames: number[] };
    w.__frames = [];
    let last = performance.now();
    const tick = (now: number) => {
      w.__frames.push(now - last);
      last = now;
      if (w.__frames.length < 400) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });
  const cx = box.x + box.width / 2;
  const cy = box.y + box.height / 2;
  await page.mouse.move(cx, cy);
  await page.mouse.down();
  for (let i = 0; i < 40; i += 1) await page.mouse.move(cx + Math.sin(i / 6) * 160, cy + Math.cos(i / 6) * 90, { steps: 2 });
  await page.mouse.up();
  for (let i = 0; i < 10; i += 1) await page.mouse.wheel(0, i < 5 ? -120 : 120);
  const frames = await page.evaluate(() => (window as unknown as { __frames: number[] }).__frames.slice(5));
  const sorted = [...frames].sort((a, b) => a - b);
  const p95 = sorted[Math.floor(sorted.length * 0.95)] ?? Infinity;
  const median = sorted[Math.floor(sorted.length / 2)] ?? Infinity;
  console.log(`200 nodes in ${renderMs} ms; pan/zoom frames: median ${median.toFixed(1)} ms, p95 ${p95.toFixed(1)} ms (${frames.length} frames)`);
  expect(renderMs).toBeLessThan(3_000);
  expect(median).toBeLessThan(25); // ≥ 40 fps typical
  expect(p95).toBeLessThan(50); // no frame-long stalls

  // clicking a node loads its neighbourhood: at 1 hop, a victim brings its phone, IP and shops
  await page.getByRole("radio", { name: "1 hop" }).click();
  await expect.poll(() => nodeCount(page)).toBeLessThan(30);
  const before = await nodeCount(page);
  const pos = await positionOf(page, "A:U0400");
  if (!pos) throw new Error("A:U0400 is not on screen");
  const fresh = await canvas.boundingBox();
  await page.mouse.click((fresh?.x ?? 0) + pos.x, (fresh?.y ?? 0) + pos.y);
  await expect.poll(() => nodeCount(page)).toBeGreaterThan(before);
  await expect(page.getByRole("button", { name: "Centre the graph here" })).toBeVisible();
});
