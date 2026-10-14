import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { openChat, staffLogin } from "./helpers";

async function scan(page: import("@playwright/test").Page, name: string) {
  const results = await new AxeBuilder({ page: page as never }).withTags(["wcag2a", "wcag2aa"]).analyze();
  const bad = results.violations.filter((v) => ["serious", "critical"].includes(v.impact ?? ""));
  expect(bad.map((v) => `${name}: ${v.id} (${v.nodes.length}) ${v.help}`), `${name} accessibility`).toEqual([]);
}

test("public pages have no serious accessibility violations", async ({ page }) => {
  await page.goto("/");
  await scan(page, "landing");
  await page.goto("/login");
  await scan(page, "login");
  await openChat(page);
  await scan(page, "guest chat");
});

test("staff screens have no serious accessibility violations", async ({ page }) => {
  await staffLogin(page, "manager@demo.hotel");
  await page.waitForURL("**/staff");
  await expect(page.getByText("Open requests", { exact: true })).toBeVisible();
  await scan(page, "overview");
  for (const path of ["tickets", "reservations", "conversations", "approvals", "integrations"]) {
    await page.goto(`/staff/${path}`);
    await page.waitForLoadState("networkidle");
    await scan(page, path);
  }
});

test("mobile layout: the chat fits the screen without horizontal scrolling", async ({ browser }) => {
  const ctx = await browser.newContext({ viewport: { width: 375, height: 700 } });
  const page = await ctx.newPage();
  await openChat(page);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await expect(page.getByLabel("Message")).toBeVisible();
});
