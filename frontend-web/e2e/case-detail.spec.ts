import { expect, test } from "@playwright/test";

// Mock mode uses the demo analyst; against the real stack pass a seeded analyst's login.
const EMAIL = process.env.E2E_EMAIL ?? "analyst@demo.fraudshield";
const PASSWORD = process.env.E2E_PASSWORD ?? "demo-pass-123";

test("analyst logs in, opens the first alert and confirms fraud", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/dashboard$/);

  await page.getByRole("link", { name: "Alert queue" }).click();
  const first = page.getByRole("link", { name: /^Open \S+/ }).first();
  await expect(first).toBeVisible();
  const txnId = ((await first.getAttribute("aria-label")) ?? "").replace(/^Open /, "");
  expect(txnId).not.toBe("");
  await first.click();

  await expect(page).toHaveURL(/\/dashboard\/cases\/[\w-]+$/);
  await expect(page.getByRole("heading", { name: "Case detail" })).toBeVisible();
  await expect(page.getByText(txnId, { exact: true })).toBeVisible();
  await expect(page.getByText("What drove the score")).toBeVisible();

  await page.getByLabel(/^Note/).fill("e2e: confirmed with the customer by phone");
  await page.getByRole("button", { name: "Confirm fraud" }).click();

  await expect(page.getByRole("status").filter({ hasText: `${txnId} · the alert queue is updated` })).toBeVisible();
  await expect(page.getByRole("button", { name: "Confirm fraud" })).toHaveCount(0);
  await expect(page.getByText("Confirmed as fraud").first()).toBeVisible();

  await page.getByRole("link", { name: "Alert queue" }).first().click();
  await expect(page).toHaveURL(/\/dashboard\/alerts$/);
  await expect(page.getByRole("link", { name: /^Open \S+/ }).first()).toBeVisible();
  await expect(page.getByRole("link", { name: `Open ${txnId}` })).toHaveCount(0); // it left the queue
});
