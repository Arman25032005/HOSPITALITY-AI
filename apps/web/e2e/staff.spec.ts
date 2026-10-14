import { expect, test } from "@playwright/test";
import { API, apiToken, hotelId, iso, PASSWORD, staffLogin } from "./helpers";

test("wrong credentials are rejected with a clear message and no session", async ({ page }) => {
  await staffLogin(page, "manager@demo.hotel", "not-the-password");
  await expect(page.getByRole("alert").filter({ hasText: "Invalid email" })).toHaveText("Invalid email or password");
  await expect(page).toHaveURL(/\/login/);
  expect((await page.context().cookies()).some((c) => c.name === "hos_session")).toBeFalsy();
});

test("the session token is an httpOnly cookie and is not readable by page scripts", async ({ page }) => {
  await staffLogin(page, "manager@demo.hotel");
  await page.waitForURL("**/staff");
  const cookie = (await page.context().cookies()).find((c) => c.name === "hos_session");
  expect(cookie?.httpOnly).toBe(true);
  expect(cookie?.sameSite).toBe("Lax");
  expect(await page.evaluate(() => document.cookie)).not.toContain("hos_session");
  expect(await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage }))).not.toContain("eyJ");
});

test("signed-out visitors are sent to sign in, and the proxy refuses unsafe requests", async ({ page, request }) => {
  await page.goto("/staff/tickets");
  await expect(page).toHaveURL(/\/login\?next=/);
  expect((await request.get("/api/bff/hotels")).status()).toBe(401);
  expect((await request.post("/api/bff/hotels/x/tickets", { data: {} })).status()).toBe(403); // no CSRF header
  expect((await request.get("/api/bff/auth/login")).status()).toBe(404);                      // not an allowed path
  expect((await request.get("/api/guest/hotels")).status()).toBe(404);
});

test("department staff only see their own areas; other pages show permission-denied", async ({ page }) => {
  await staffLogin(page, "housekeeping@demo.hotel");
  await page.waitForURL("**/staff");
  const nav = page.getByRole("navigation", { name: "Main" });
  await expect(nav.getByRole("link", { name: "Tickets" })).toBeVisible();
  await expect(nav.getByRole("link", { name: "Reservations" })).toHaveCount(0);
  await expect(nav.getByRole("link", { name: "Team" })).toHaveCount(0);
  await page.goto("/staff/reservations"); // typing the URL does not grant access
  await expect(page.getByText("You don't have access")).toBeVisible();
  await page.goto("/staff/tickets");
  await expect(page.getByRole("heading", { name: "Tickets" })).toBeVisible();
});

test("managers get live analytics on the overview", async ({ page }) => {
  await staffLogin(page, "manager@demo.hotel");
  await page.waitForURL("**/staff");
  await expect(page.getByText("Open requests", { exact: true })).toBeVisible();
  await expect(page.getByText("Department workload (open)")).toBeVisible();
  await page.getByLabel("Time window").selectOption("30");
  await expect(page.getByText("resolved, last 30d")).toBeVisible();
});

test("reservations: search, change dates, then cancel with confirmation", async ({ page, request }) => {
  const token = await apiToken(request, "reception@demo.hotel");
  const h = { authorization: `Bearer ${token}` };
  const hid = await hotelId(request, token);
  const g = await (await request.post(`${API}/api/v1/hotels/${hid}/guests`, { headers: h, data: { full_name: "Pat Later" } })).json();
  const types = await (await request.get(`${API}/api/v1/hotels/${hid}/availability`, { headers: h, params: { check_in: iso(60), check_out: iso(62), adults: 2 } })).json();
  const res = await (await request.post(`${API}/api/v1/hotels/${hid}/reservations`, { headers: h, data: { check_in: iso(60), check_out: iso(62), adults: 2, guest_id: g.id, room_type_id: types[0].room_type_id } })).json();

  await staffLogin(page, "reception@demo.hotel");
  await page.waitForURL("**/staff");
  await page.goto("/staff/reservations");
  await page.getByLabel("Search").fill("pat later");
  await page.getByRole("button", { name: `Open reservation ${res.confirmation_code}` }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "Change dates" }).click();
  await dialog.getByLabel("Check-out").fill(iso(63));
  await dialog.getByRole("button", { name: "Save" }).click();
  await expect(dialog.getByText("Reservation updated")).toBeVisible();
  await expect(dialog.getByText(/3 nights|→/)).toBeVisible();
  await dialog.getByRole("button", { name: "Cancel reservation" }).click();
  await expect(dialog.getByText(/rooms are released immediately/)).toBeVisible();
  await dialog.getByRole("button", { name: "Yes, cancel it" }).click();
  await expect(dialog.getByText("Reservation cancelled")).toBeVisible();
  await dialog.getByRole("button", { name: "Close" }).click();
  await expect(page.getByRole("row", { name: /Pat Later/ }).getByText("Cancelled")).toBeVisible();
  await page.getByRole("tab", { name: "Inventory" }).click();
  await expect(page.getByRole("table", { name: /Booked rooms/ })).toBeVisible();
});

test("tickets: filters narrow the list and an empty result is explained", async ({ page }) => {
  await staffLogin(page, "reception@demo.hotel");
  await page.waitForURL("**/staff");
  await page.goto("/staff/tickets");
  await page.getByLabel("Search").fill("zzz-no-such-ticket-zzz");
  await expect(page.getByText("No tickets found")).toBeVisible();
  await page.getByLabel("Search").fill("");
  await page.getByLabel("Priority").selectOption("urgent");
  await expect(page.getByRole("heading", { name: "Tickets" })).toBeVisible();
});

test("sign out ends the session", async ({ page }) => {
  await staffLogin(page, "manager@demo.hotel");
  await page.waitForURL("**/staff");
  await page.getByRole("button", { name: "Sign out" }).click();
  await page.waitForURL("**/login");
  await page.goto("/staff");
  await expect(page).toHaveURL(/\/login/);
  void PASSWORD;
});
