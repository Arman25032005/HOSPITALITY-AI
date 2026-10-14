import { APIRequestContext, expect, Page } from "@playwright/test";

export const API = process.env.E2E_API ?? "http://localhost:8801";
export const PASSWORD = "demo-password-123";
export const HOTEL_ID = "425e223a-4481-53c9-9701-4a6e365c7f2d"; // uuid5 of the seeded demo hotel

export const iso = (offset: number) => new Date(Date.now() + offset * 864e5).toISOString().slice(0, 10);

export async function apiToken(request: APIRequestContext, email: string): Promise<string> {
  const r = await request.post(`${API}/api/v1/auth/login`, { data: { email, password: PASSWORD } });
  expect(r.ok()).toBeTruthy();
  return (await r.json()).access_token;
}

export async function hotelId(request: APIRequestContext, token: string): Promise<string> {
  const r = await request.get(`${API}/api/v1/hotels`, { headers: { authorization: `Bearer ${token}` } });
  return (await r.json())[0].id;
}

/** Creates a checked-in stay for `name` through the staff API and returns its confirmation code. */
export async function checkedInStay(request: APIRequestContext, name: string): Promise<string> {
  const token = await apiToken(request, "reception@demo.hotel");
  const h = { authorization: `Bearer ${token}` };
  const hid = await hotelId(request, token);
  const g = await (await request.post(`${API}/api/v1/hotels/${hid}/guests`, { headers: h, data: { full_name: name } })).json();
  const types = await (await request.get(`${API}/api/v1/hotels/${hid}/availability`, {
    headers: h, params: { check_in: iso(0), check_out: iso(2), adults: 2 } })).json();
  const res = await (await request.post(`${API}/api/v1/hotels/${hid}/reservations`, {
    headers: h, data: { check_in: iso(0), check_out: iso(2), adults: 2, guest_id: g.id, room_type_id: types[0].room_type_id } })).json();
  const ci = await request.post(`${API}/api/v1/hotels/${hid}/reservations/${res.id}/check-in`, { headers: h, data: {} });
  expect(ci.ok()).toBeTruthy();
  return res.confirmation_code;
}

export async function staffLogin(page: Page, email: string, password = PASSWORD) {
  await page.goto("/login");
  await page.getByLabel("Work email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
}

export async function openChat(page: Page) {
  await page.goto("/");
  await page.getByRole("link", { name: /Demo Grand Hotel/ }).click();
  await expect(page.getByRole("heading", { name: "Demo Grand Hotel" })).toBeVisible();
}

export async function say(page: Page, text: string, waitFor: string | RegExp) {
  await page.getByLabel("Message").fill(text);
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.getByRole("log").getByText(waitFor).last()).toBeVisible();
}
