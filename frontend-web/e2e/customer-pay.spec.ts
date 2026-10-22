import { devices, expect, type Page, test } from "@playwright/test";

// Spec 07 §6: the customer app on a phone. Mock mode uses the demo customer; against the real
// stack pass a seeded customer's login and a payee that customer pays normally.
const EMAIL = process.env.E2E_CUSTOMER_EMAIL ?? "customer@demo.fraudshield";
const PASSWORD = process.env.E2E_CUSTOMER_PASSWORD ?? "demo-pass-123";
const NORMAL_PAYEE = process.env.E2E_NORMAL_PAYEE ?? "grocer.mart@okhdfcbank";

// The phone's screen and touch, in the project's Chrome (a file can't switch the browser type).
const { viewport, userAgent, deviceScaleFactor, isMobile, hasTouch } = devices["Pixel 7"];
test.use({ viewport, userAgent, deviceScaleFactor, isMobile, hasTouch });

async function signIn(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/app$/);
}

async function pay(page: Page, vpa: string, amount: string) {
  await page.getByRole("link", { name: "Pay", exact: true }).click();
  await page.getByLabel("Pay to (UPI ID)").fill(vpa);
  await page.getByLabel("Amount").fill(amount);
  await page.getByRole("button", { name: "Pay", exact: true }).click();
}

test("a normal ₹200 payment succeeds", async ({ page }) => {
  await signIn(page);
  await page.getByRole("link", { name: "Settings" }).click();
  await page.getByRole("button", { name: "Normal" }).click();

  const started = Date.now();
  await pay(page, NORMAL_PAYEE, "200");
  await expect(page.getByRole("heading", { name: "Payment sent" })).toBeVisible();
  console.log(`₹200 -> "Payment sent" in ${Date.now() - started} ms`);
  await expect(page.getByText("₹200", { exact: true })).toBeVisible();

  await page.getByRole("link", { name: "Back to home" }).click();
  await expect(page.getByRole("heading", { name: /^Hello/ })).toBeVisible();
});

test("a scam-call payment shows the warning, and Cancel keeps the money", async ({ page }) => {
  await signIn(page);
  await page.getByRole("link", { name: "Settings" }).click();
  await page.getByRole("button", { name: "Scam call" }).click();
  await expect(page.getByRole("switch", { name: "On a phone call" })).toHaveAttribute("aria-checked", "true");

  await page.getByRole("link", { name: "Pay", exact: true }).click();
  await expect(page.getByText(/^Simulated:/)).toBeVisible();
  await page.getByLabel("Pay to (UPI ID)").fill("refund.help@okbank");
  await page.getByLabel("Amount").fill("48500");
  await page.getByRole("button", { name: "Pay", exact: true }).click();

  await expect(page).toHaveURL(/\/app\/pay\/[^/]+\/warning$/);
  const alert = page.getByRole("alert");
  await expect(alert.getByRole("heading", { name: /We have paused this payment|This payment may be a scam/ })).toBeVisible();
  await expect(page.getByText("Why we are worried")).toBeVisible();

  await page.getByRole("button", { name: "Cancel payment" }).click();
  await expect(page.getByText("Your money is safe.")).toBeVisible();
  await expect(page).toHaveURL(/\/app$/);

  await page.getByRole("link", { name: "Settings" }).click();
  await page.getByRole("button", { name: "Normal" }).click(); // leave the browser as found
});
