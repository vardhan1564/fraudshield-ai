import { devices, expect, test } from "@playwright/test";

// Spec 07 §5 digital-arrest demo on a phone (T5.4): Settings "On a call" + "Unknown caller" +
// "Screen sharing" → pay ₹48,500 to refund.help@okbank → Scam Coach → YES to the first question
// → red HOLD screen with "Call my bank". Mock mode uses the demo customer; against the real stack
// pass a seeded customer's login.
const EMAIL = process.env.E2E_CUSTOMER_EMAIL ?? "customer@demo.fraudshield";
const PASSWORD = process.env.E2E_CUSTOMER_PASSWORD ?? "demo-pass-123";

const { viewport, userAgent, deviceScaleFactor, isMobile, hasTouch } = devices["Pixel 7"];
test.use({ viewport, userAgent, deviceScaleFactor, isMobile, hasTouch });

test("digital arrest: Scam Coach, one YES, red HOLD screen with Call my bank", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/app$/);

  // the three switches of the acceptance script, one by one
  await page.getByRole("link", { name: "Settings" }).click();
  await page.getByRole("button", { name: "Normal" }).click();
  for (const name of ["On a phone call", "Caller is not in my contacts", "Sharing my screen"]) {
    const toggle = page.getByRole("switch", { name });
    if ((await toggle.getAttribute("aria-checked")) !== "true") await toggle.click();
    await expect(toggle).toHaveAttribute("aria-checked", "true");
  }

  await page.getByRole("link", { name: "Pay", exact: true }).click();
  await page.getByLabel("Pay to (UPI ID)").fill("refund.help@okbank");
  await page.getByLabel("Amount").fill("48500");
  await page.getByRole("button", { name: "Pay", exact: true }).click();

  // Scam Coach appears, full screen, with the cooling-off timer and Cancel as the biggest button
  await expect(page).toHaveURL(/\/app\/pay\/[^/]+\/coach$/);
  await expect(page.getByRole("heading", { name: /^Wait\. Answer \d quick questions first$/ })).toBeVisible();
  await expect(page.getByRole("timer")).toContainText("Cooling-off time");
  const cancel = await page.getByRole("button", { name: "Cancel payment" }).boundingBox();
  const check = await page.getByRole("button", { name: "Check my answers" }).boundingBox();
  expect((cancel?.height ?? 0) * (cancel?.width ?? 0)).toBeGreaterThan((check?.height ?? 0) * (check?.width ?? 0));

  // YES to the first question, NO to the rest
  const groups = page.getByRole("radiogroup");
  const count = await groups.count();
  expect(count).toBeGreaterThanOrEqual(2);
  await groups.nth(0).getByRole("radio", { name: "Yes" }).click();
  for (let i = 1; i < count; i += 1) await groups.nth(i).getByRole("radio", { name: "No" }).click();
  await page.getByRole("button", { name: "Check my answers" }).click();

  await expect(page).toHaveURL(/\/app\/pay\/[^/]+\/warning$/);
  await expect(page.getByRole("alert").getByRole("heading", { name: "We stopped this payment to protect you" })).toBeVisible();
  await expect(page.getByRole("link", { name: /^Call my bank/ })).toHaveAttribute("href", /^tel:/);
  await expect(page.getByRole("button", { name: /Pay anyway/ })).toHaveCount(0);

  await page.getByRole("button", { name: "Cancel payment" }).click();
  await expect(page.getByText("Your money is safe.")).toBeVisible();
  await page.getByRole("link", { name: "Settings" }).click();
  await page.getByRole("button", { name: "Normal" }).click(); // leave the browser as found
});
