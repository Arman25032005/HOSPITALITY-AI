import AxeBuilder from "@axe-core/playwright";
import { expect, Page, test } from "@playwright/test";
import { API, apiToken, hotelId, staffLogin } from "./helpers";

async function scan(page: Page, name: string) {
  const results = await new AxeBuilder({ page: page as never }).withTags(["wcag2a", "wcag2aa"]).analyze();
  const bad = results.violations.filter((v) => ["serious", "critical"].includes(v.impact ?? ""));
  expect(bad.map((v) => `${name}: ${v.id} (${v.nodes.length}) ${v.help}`), `${name} accessibility`).toEqual([]);
}

async function asManager(page: Page) {
  await staffLogin(page, "manager@demo.hotel");
  await page.waitForURL("**/staff");
}

/** A guest asks questions through the public API so the insights screens have real data. */
async function guestAsks(request: import("@playwright/test").APIRequestContext, questions: string[]) {
  const s = await (await request.post(`${API}/api/v1/chat/sessions`, { data: { org_slug: "demo-group", hotel_slug: "demo-grand" } })).json();
  const headers = { authorization: `Bearer ${s.token}` };
  for (const [i, text] of questions.entries()) {
    const r = await request.post(`${API}/api/v1/chat/messages`, { headers, data: { text, client_message_id: `e2e-${Date.now()}-${i}` } });
    expect(r.ok()).toBeTruthy();
  }
}

const SAUNA = "# Sauna\n\n## Hours\n\nThe sauna is open daily from 9am to 5pm and is free for guests.\n";

test.describe.serial("knowledge administration", () => {
  test("upload, inspect, test retrieval, deactivate, then delete", async ({ page }) => {
    await asManager(page);
    await page.goto("/staff/knowledge");
    await expect(page.getByRole("heading", { name: "Knowledge" })).toBeVisible();
    const table = page.getByRole("table", { name: "Knowledge documents and their versions" });
    await expect(table.getByRole("button", { name: /Open Stay Policies/ })).toBeVisible();

    const form = page.getByRole("form", { name: "Upload document" });
    await form.getByLabel("File").setInputFiles({ name: "sauna.md", mimeType: "text/markdown", buffer: Buffer.from(SAUNA) });
    await form.getByLabel("Title").fill("Sauna Guide");
    await form.getByLabel("Type").selectOption("amenity");
    await form.getByRole("button", { name: "Upload and index" }).click();
    await expect(page.getByText("“Sauna Guide” ingested as version 1.")).toBeVisible();
    await expect(table.getByRole("button", { name: /Open Sauna Guide version 1/ })).toBeVisible();

    // ingestion history is visible and the document is searchable exactly as a guest would see it
    await table.getByRole("button", { name: /Open Sauna Guide version 1/ }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.getByText("Passages indexed")).toBeVisible();
    await expect(dialog.getByRole("list", { name: "Ingestion jobs" }).getByText("Succeeded")).toBeVisible();
    await dialog.getByRole("button", { name: "Close", exact: true }).first().click();

    const tester = page.getByRole("form", { name: "Test retrieval" });
    await tester.getByLabel("Question").fill("What are the sauna hours?");
    await tester.getByRole("button", { name: "Search" }).click();
    await expect(page.getByText("Assistant answer")).toBeVisible();
    await expect(page.getByText("Answered", { exact: true })).toBeVisible();
    await expect(page.getByText(/9am to 5pm/).first()).toBeVisible();
    await expect(page.getByText(/Sources: Sauna Guide v1/)).toBeVisible();

    await scan(page, "knowledge");

    // deactivation needs confirmation, then the assistant stops using it
    await table.getByRole("button", { name: /Deactivate Sauna Guide version 1/ }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Deactivate" }).click();
    await expect(page.getByText("“Sauna Guide” deactivated.")).toBeVisible();
    await expect(table.getByRole("row", { name: /Sauna Guide/ }).getByText("Inactive")).toBeVisible();
    await tester.getByRole("button", { name: "Search" }).click();
    await expect(page.getByText("Abstained")).toBeVisible();

    await table.getByRole("button", { name: /Delete Sauna Guide version 1/ }).click();
    await expect(page.getByRole("dialog").getByText(/cannot be undone/)).toBeVisible();
    await page.getByRole("dialog").getByRole("button", { name: "Delete" }).click();
    await expect(page.getByText("“Sauna Guide” deleted.")).toBeVisible();
    await expect(table.getByRole("button", { name: /Sauna Guide/ })).toHaveCount(0);
  });

  test("a re-upload under the same key becomes a new version", async ({ page }) => {
    await asManager(page);
    await page.goto("/staff/knowledge");
    const form = page.getByRole("form", { name: "Upload document" });
    for (const [n, text] of [[1, "# Pool\n\n## Hours\n\nThe pool opens at 8am.\n"], [2, "# Pool\n\n## Hours\n\nThe pool opens at 7am.\n"]] as const) {
      await form.getByLabel("File").setInputFiles({ name: "pool.md", mimeType: "text/markdown", buffer: Buffer.from(text) });
      await form.getByLabel("Title").fill("Pool Notes");
      await form.getByLabel("Document key").fill("pool-notes");
      await form.getByRole("button", { name: "Upload and index" }).click();
      await expect(page.getByText(`“Pool Notes” ingested as version ${n}.`)).toBeVisible();
    }
    const table = page.getByRole("table", { name: "Knowledge documents and their versions" });
    await expect(table.getByRole("button", { name: /Open Pool Notes version 2/ })).toBeVisible();
    await expect(table.getByRole("button", { name: /Open Pool Notes version 1/ })).toBeVisible();
    await expect(table.getByText("older version")).toBeVisible();
  });

  test("unsupported files are rejected with a clear message", async ({ page }) => {
    await asManager(page);
    await page.goto("/staff/knowledge");
    const form = page.getByRole("form", { name: "Upload document" });
    await form.getByLabel("File").setInputFiles({ name: "evil.exe", mimeType: "application/octet-stream", buffer: Buffer.from("MZ\x00\x00") });
    await form.getByLabel("Title").fill("Not a document");
    await form.getByRole("button", { name: "Upload and index" }).click();
    await expect(page.getByRole("alert").filter({ hasText: /not supported|unsupported|file type/i })).toBeVisible();
  });
});

test("AI insights show real activity, sanitized traces, gaps and the audit log", async ({ page, request }) => {
  await guestAsks(request, ["What time is check-out?", "Do you have a casino?", "Do you have a casino?", "I want to speak to a manager"]);
  await asManager(page);
  await page.goto("/staff/insights");
  await expect(page.getByRole("heading", { name: "AI insights" })).toBeVisible();
  await expect(page.getByText("Resolved by the assistant")).toBeVisible();
  await expect(page.getByText("Tool success")).toBeVisible();
  await expect(page.getByRole("list", { name: "Outcomes" })).toBeVisible();
  await scan(page, "insights overview");

  await page.getByRole("tab", { name: "Conversations" }).click();
  const turns = page.getByRole("table", { name: "Recent assistant turns" });
  await expect(turns.getByRole("row").nth(1)).toBeVisible();
  await page.getByLabel("Outcome").selectOption("abstained");
  await expect(turns.getByText("Abstained").first()).toBeVisible();
  await page.getByLabel("Outcome").selectOption("");
  await turns.getByRole("button", { name: /View trace/ }).first().click();
  const dialog = page.getByRole("dialog", { name: "Turn trace" });
  await expect(dialog.getByText("redacted server-side")).toBeVisible();
  await expect(dialog.getByText("Tool calls")).toBeVisible();
  await scan(page, "insights trace");
  await dialog.getByRole("button", { name: "Close", exact: true }).first().click();

  await page.getByRole("tab", { name: "Tools" }).click();
  await expect(page.getByRole("table", { name: "Tool success rates" }).getByText("search_knowledge")).toBeVisible();

  await page.getByRole("tab", { name: "Unanswered" }).click();
  await expect(page.getByRole("table", { name: /could not answer/ }).getByText("Do you have a casino?").first()).toBeVisible();

  await page.getByRole("tab", { name: "Low-rated" }).click();
  await expect(page.getByText(/No low-rated answers|Guest asked/).first()).toBeVisible();

  await page.getByRole("tab", { name: "Audit log" }).click();
  await expect(page.getByRole("table", { name: "Audit log" })).toBeVisible();
  await scan(page, "insights audit");
});

test.describe.serial("team management", () => {
  const email = `e2e.newhire.${Date.now()}@demo.hotel`;
  const tempPw = "Harbor-Lantern-Quartz-42";

  test("only administrators see the Team area", async ({ page }) => {
    await asManager(page);
    await expect(page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Team" })).toHaveCount(0);
    await page.goto("/staff/team");
    await expect(page.getByText("Only administrators can manage the team.")).toBeVisible();
  });

  test("admin adds, edits, disables and resets a user; weak passwords are refused", async ({ page, browser }) => {
    await staffLogin(page, "admin@demo.hotel");
    await page.waitForURL("**/staff");
    await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Team" }).click();
    await expect(page.getByRole("table", { name: "Staff accounts" })).toBeVisible();
    await scan(page, "team");

    await page.getByRole("button", { name: "Add staff member" }).click();
    let dialog = page.getByRole("dialog", { name: "Add staff member" });
    await dialog.getByLabel("Full name").fill("Nora Newhire");
    await dialog.getByLabel("Email").fill(email);
    await dialog.getByLabel("Role").selectOption("receptionist");
    await dialog.getByLabel("Temporary password").fill("password1234");
    await dialog.getByRole("checkbox", { name: /Demo Grand Hotel/ }).check();
    await dialog.getByRole("button", { name: "Save" }).click();
    await expect(dialog.getByRole("alert")).toBeVisible(); // weak password rejected, nothing created
    await dialog.getByLabel("Temporary password").fill(tempPw);
    await dialog.getByRole("button", { name: "Save" }).click();
    await expect(page.getByText("Added Nora Newhire.")).toBeVisible();
    const row = page.getByRole("row", { name: /Nora Newhire/ });
    await expect(row.getByText("Active")).toBeVisible();

    // the new account can sign in with the temporary password
    const ctx = await browser.newContext();
    const other = await ctx.newPage();
    await staffLogin(other, email, tempPw);
    await other.waitForURL("**/staff");

    // disabling it ends that session immediately
    await row.getByRole("button", { name: "Edit Nora Newhire" }).click();
    dialog = page.getByRole("dialog", { name: "Edit Nora Newhire" });
    await dialog.getByLabel(/Account active/).uncheck();
    await dialog.getByRole("button", { name: "Save" }).click();
    await expect(page.getByText(/Saved Nora Newhire/)).toBeVisible();
    await expect(row.getByText("Disabled")).toBeVisible();
    await other.goto("/staff/tickets");
    await expect(other).toHaveURL(/\/login/);
    await ctx.close();

    // an administrator cannot lock themselves out
    await page.getByRole("button", { name: "Edit Dana Admin" }).click();
    const own = page.getByRole("dialog", { name: "Edit Dana Admin" });
    await expect(own.getByLabel("Role")).toBeDisabled();
    await expect(own.getByLabel(/Account active/)).toHaveCount(0);
    await own.getByRole("button", { name: "Cancel" }).click();

    await row.getByRole("button", { name: "Reset password for Nora Newhire" }).click();
    dialog = page.getByRole("dialog", { name: "Reset password for Nora Newhire" });
    await dialog.getByLabel("New temporary password").fill("short");
    await dialog.getByRole("button", { name: "Reset password" }).click();
    await expect(dialog.getByRole("alert")).toBeVisible();
    await dialog.getByLabel("New temporary password").fill("Copper-Meadow-Violet-77");
    await dialog.getByRole("button", { name: "Reset password" }).click();
    await expect(page.getByText(/Password reset for Nora Newhire/)).toBeVisible();
  });

  test("changing your own password keeps you signed in and replaces the old password", async ({ page, browser, request }) => {
    // A throwaway account: seeded users keep their passwords so the other specs stay independent.
    const adminToken = await apiToken(request, "admin@demo.hotel");
    const hid = await hotelId(request, adminToken);
    const mine = `e2e.pw.${Date.now()}@demo.hotel`;
    const pw = "Copper-Meadow-Violet-77";
    const created = await request.post(`${API}/api/v1/users`, {
      headers: { authorization: `Bearer ${adminToken}` },
      data: { email: mine, full_name: "Pia Password", role: "receptionist", password: tempPw, assignments: [{ hotel_id: hid }] },
    });
    expect(created.status()).toBe(201);
    await staffLogin(page, mine, tempPw);
    await page.waitForURL("**/staff");
    await page.goto("/staff/account");
    const form = page.getByRole("form", { name: "Change password" });
    await form.getByLabel("Current password").fill(tempPw);
    await form.getByLabel("New password", { exact: true }).fill("short");
    await form.getByLabel("Confirm new password").fill("short");
    await form.getByRole("button", { name: "Change password" }).click();
    await expect(form.getByRole("alert")).toBeVisible();
    await form.getByLabel("Current password").fill("wrong-current-password");
    await form.getByLabel("New password", { exact: true }).fill(pw);
    await form.getByLabel("Confirm new password").fill(pw);
    await form.getByRole("button", { name: "Change password" }).click();
    await expect(form.getByRole("alert")).toContainText(/incorrect/i);
    await form.getByLabel("Current password").fill(tempPw);
    await form.getByLabel("New password", { exact: true }).fill(pw);
    await form.getByLabel("Confirm new password").fill(pw);
    await form.getByRole("button", { name: "Change password" }).click();
    await expect(page.getByText(/Password changed/)).toBeVisible();
    await scan(page, "account");

    // still signed in on this browser…
    await page.goto("/staff/tickets");
    await expect(page.getByRole("heading", { name: "Tickets" })).toBeVisible();
    // …and the old password no longer works
    const ctx = await browser.newContext();
    const fresh = await ctx.newPage();
    await staffLogin(fresh, mine, tempPw);
    await expect(fresh.getByRole("alert").filter({ hasText: "Invalid email" })).toBeVisible();
    await ctx.close();
  });
});
