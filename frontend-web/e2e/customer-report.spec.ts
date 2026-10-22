import { devices, expect, test } from "@playwright/test";

// T4.6 done-when: a report from /app/report raises that payee's report count.
const EMAIL = process.env.E2E_CUSTOMER_EMAIL ?? "customer@demo.fraudshield";
const PASSWORD = process.env.E2E_CUSTOMER_PASSWORD ?? "demo-pass-123";

const { viewport, userAgent, deviceScaleFactor, isMobile, hasTouch } = devices["Pixel 7"];
test.use({ viewport, userAgent, deviceScaleFactor, isMobile, hasTouch });

test("a report from /app/report raises that payee's report count", async ({ page }) => {
  const vpa = `e2e.scam.${Date.now().toString(36)}@ybl`; // new each run: one report per customer per payee
  await page.goto("/login");
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/app$/);

  // the count before, as the Pay screen sees it (the app's own API client and token)
  type Payees = { payeeReputation: (vpa: string) => Promise<{ report_count: number }> };
  const countOf = () =>
    page.evaluate(async ({ v, module }) => {
      const { payeeReputation } = (await import(/* @vite-ignore */ module)) as Payees;
      return (await payeeReputation(v)).report_count;
    }, { v: vpa, module: "/src/shared/api/endpoints/payees.ts" }); // Vite serves the source in dev
  const before = await countOf();

  await page.getByRole("link", { name: "Report" }).click();
  await page.getByLabel("Their UPI ID").fill(vpa);
  await page.getByRole("radio", { name: "Fake police or CBI call" }).click();
  await page.getByLabel(/Number that called you/).fill("9812345621");
  await page.getByRole("button", { name: "Send report" }).click();

  await expect(page.getByRole("heading", { name: "Thank you for reporting" })).toBeVisible();
  await expect(page.getByText(/have now reported|has now reported/)).toContainText(String(before + 1));
  expect(await countOf()).toBe(before + 1);
  console.log(`report count for ${vpa}: ${before} -> ${before + 1}`);

  await page.getByRole("link", { name: "Back to home" }).click();
  await expect(page.getByRole("heading", { name: /^Hello/ })).toBeVisible();
});
