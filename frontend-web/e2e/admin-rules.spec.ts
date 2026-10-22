import { readFile } from "node:fs/promises";

import { expect, test } from "@playwright/test";

// T4.7: the rules form rejects invalid conditions before submit (spec 06 §4); a valid rule is
// saved, shows in the audit log and its CSV, and is removed again (leaves the system as found).
const EMAIL = process.env.E2E_ADMIN_EMAIL ?? "admin@demo.fraudshield";
const PASSWORD = process.env.E2E_ADMIN_PASSWORD ?? "demo-pass-123";

test("admin: invalid rule blocked, valid rule saved, audited, in the CSV, then deleted", async ({ page }) => {
  const name = `E2E rule ${Date.now().toString(36)}`;
  const posts: string[] = [];
  page.on("request", (r) => {
    if (r.method() === "POST" && r.url().includes("/api/v1/rules")) posts.push(r.url());
  });

  await page.goto("/login");
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/dashboard$/);
  await page.getByRole("link", { name: "Rules" }).click();
  await page.getByRole("button", { name: "New rule" }).click();

  // invalid: a yes/no feature with ">" and no value -> blocked, nothing sent
  await page.getByLabel("Name").fill(name);
  await page.getByLabel("Feature, condition 1").selectOption("amount");
  await page.getByLabel("Value, condition 1").fill("lots");
  await page.getByRole("button", { name: "Save rule" }).click();
  await expect(page.getByText("Enter a number, e.g. 50000 or 0.5.")).toBeVisible();
  await expect(page.getByText("Fix the highlighted fields. Nothing was sent.")).toBeVisible();
  expect(posts).toHaveLength(0);

  // valid: amount between 777 and 778 -> Held (no real payment uses that amount by accident)
  await page.getByLabel("Value, condition 1").fill("777");
  await page.getByLabel("Operator, condition 1").selectOption(">=");
  await page.getByRole("button", { name: "Add condition" }).click();
  await page.getByLabel("Feature, condition 2").selectOption("amount");
  await page.getByLabel("Operator, condition 2").selectOption("<");
  await page.getByLabel("Value, condition 2").fill("778");
  await page.getByRole("radio", { name: "Held" }).click();
  await expect(page.getByLabel("JSON sent to the API")).toContainText('"value": 778');
  await page.getByRole("button", { name: "Save rule" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Rule created" })).toBeVisible();
  const row = page.getByRole("row").filter({ hasText: name });
  await expect(row).toContainText("amount >= 777 AND amount < 778");
  expect(posts).toHaveLength(1);

  // real stack only (the mock API doesn't run rules): the new rule changes a real payment's decision
  if (process.env.E2E_BASE_URL && process.env.E2E_CUSTOMER_EMAIL) {
    const api = `${process.env.E2E_BASE_URL}/api/v1`;
    const login = await page.request.post(`${api}/auth/login`, {
      data: { email: process.env.E2E_CUSTOMER_EMAIL, password: process.env.E2E_CUSTOMER_PASSWORD },
    });
    const auth = { Authorization: `Bearer ${(await login.json()).access_token}` };
    const me = await (await page.request.get(`${api}/auth/me`, { headers: auth })).json();
    const decide = async (amount: number) => {
      const r = await page.request.post(`${api}/transactions/score`, {
        headers: auth,
        data: {
          txn_id: `E2E-RULE-${Date.now().toString(36)}-${amount}`, user_id: me.id, amount, channel: "UPI",
          payee_vpa: process.env.E2E_NORMAL_PAYEE ?? "sharma.kirana.1@okhdfc", merchant_category: "P2P",
          device_id: `D-phone-${me.id}`, timestamp: new Date().toISOString(), context: {},
          behaviour: { typing_speed_z: 0, session_len_s: 60, login_to_pay_s: 60 },
        },
      });
      return (await r.json()).decision as string;
    };
    expect(await decide(777.5)).toBe("HOLD"); // inside the rule
    expect(await decide(200)).toBe("APPROVE"); // outside it, as before
    console.log("real payment ₹777.50 with the UI rule: HOLD; ₹200: APPROVE");
  }

  // audited, and in the CSV export
  await page.getByRole("link", { name: "Audit log" }).click();
  await page.getByLabel("Action", { exact: true }).selectOption("rule.create");
  const created = page.getByRole("row").filter({ hasText: "rule.create" }).first();
  await expect(created).toBeVisible();
  await created.getByRole("button", { name: /Show before and after/ }).click();
  await expect(page.getByText(name).first()).toBeVisible();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export CSV" }).click();
  const csv = await readFile((await (await download).path()) ?? "", "utf8");
  expect(csv.split("\n")[0]).toContain("action");
  expect(csv).toContain(name);

  // remove it again
  await page.getByRole("link", { name: "Rules" }).click();
  await page.getByRole("button", { name: `Delete rule “${name}”` }).click();
  await page.getByRole("button", { name: "Delete", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "Rule deleted" })).toBeVisible();
  await expect(page.getByRole("row").filter({ hasText: name })).toHaveCount(0);
});
