import { expect, test } from "@playwright/test";
import { checkedInStay, iso, openChat, say, staffLogin } from "./helpers";

test("guest asks a policy question and sees where the answer came from", async ({ page }) => {
  await openChat(page);
  await say(page, "What time is breakfast on weekends?", /7:00 AM to 11:00 AM/);
  await page.getByText("Sources").last().click();
  await expect(page.getByRole("log").getByText(/Dining/).last()).toBeVisible();
  await say(page, "Do you allow helicopter landings on the roof?", /don't have verified information/);
});

test("booking only happens after the guest explicitly confirms", async ({ page }) => {
  await openChat(page);
  await say(page, "I'd like to book a room", /check-in and check-out dates/);
  await say(page, `${iso(40)} to ${iso(42)} for 2 adults`, /Deluxe King/);
  await say(page, "Deluxe King", /What name should the reservation be under/);
  await say(page, "Jane Booker", /No payment is taken online/);
  await expect(page.getByRole("log")).not.toContainText("Confirmation code");
  await page.getByRole("button", { name: "Yes, confirm" }).click();
  await expect(page.getByRole("log")).toContainText(/Confirmation code: R-[A-Z2-9]{8}/);
  const code = (await page.getByRole("log").innerText()).match(/R-[A-Z2-9]{8}/)![0];

  // The reservation is real: a manager finds it in the dashboard.
  await staffLogin(page, "manager@demo.hotel");
  await page.waitForURL("**/staff");
  await page.goto("/staff/reservations");
  await page.getByLabel("Search").fill(code);
  await expect(page.getByRole("button", { name: `Open reservation ${code}` })).toBeVisible();
  await expect(page.getByRole("cell", { name: "Jane Booker" })).toBeVisible();
});

test("maintenance request: guest verifies, staff pick it up and progress it", async ({ browser, request }) => {
  const code = await checkedInStay(request, "Alex Mercer");
  const guest = await (await browser.newContext()).newPage();
  await openChat(guest);
  await say(guest, "My AC is broken", /need to verify your stay|verify your stay/i);
  await guest.getByRole("button", { name: "Verify my stay" }).click();
  await guest.getByLabel("Confirmation code").fill("R-WRONGONE");
  await guest.getByLabel("Last name").fill("Mercer");
  await guest.getByRole("button", { name: "Verify", exact: true }).click();
  await expect(guest.getByText(/couldn't match those details/)).toBeVisible();
  await guest.getByLabel("Confirmation code").fill(code);
  await guest.getByRole("button", { name: "Verify", exact: true }).click();
  await expect(guest.getByText(/Your stay is verified/)).toBeVisible();
  await say(guest, "The AC in my room is broken and it is very hot", /logged a maintenance request/);
  const ticket = (await guest.getByRole("log").innerText()).match(/T-[A-Z2-9]{8}/)![0];

  const staff = await (await browser.newContext()).newPage();
  await staffLogin(staff, "reception@demo.hotel");
  await staff.waitForURL("**/staff");
  await staff.goto("/staff/tickets");
  await staff.getByLabel("Search").fill(ticket);
  await staff.getByRole("button", { name: `Open ticket ${ticket}` }).click();
  const dialog = staff.getByRole("dialog");
  await expect(dialog.getByText("AC in my room is broken")).toBeVisible();
  await dialog.getByLabel("Assign to").selectOption({ label: "Max Maintenance (Department staff)" });
  await expect(dialog.getByText("Ticket assigned")).toBeVisible();
  await dialog.getByRole("button", { name: "In progress" }).click();
  await expect(dialog.getByText("Status changed to In progress")).toBeVisible();
  await dialog.getByLabel("Internal notes").fill("Technician on the way");
  await dialog.getByRole("button", { name: "Add" }).click();
  await expect(dialog.getByText("Technician on the way")).toBeVisible();
  await expect(dialog.getByRole("link", { name: "View the guest conversation" })).toBeVisible();
  await expect(dialog.getByText(/Status changed/).first()).toBeVisible();

  // The guest sees the stored state, not a guess.
  await say(guest, "What is the status of my request?", /in progress/);
});

test("late checkout stays pending until staff decide, then the guest is told exactly that", async ({ browser, request }) => {
  const code = await checkedInStay(request, "Dana Lateout");
  const guest = await (await browser.newContext()).newPage();
  await openChat(guest);
  await guest.getByRole("button", { name: "Verify my stay" }).click();
  await guest.getByLabel("Confirmation code").fill(code);
  await guest.getByLabel("Last name").fill("Lateout");
  await guest.getByRole("button", { name: "Verify", exact: true }).click();
  await expect(guest.getByText(/Your stay is verified/)).toBeVisible();
  await say(guest, "Could I have a late checkout until 2 pm please?", /NOT confirmed yet/);
  await say(guest, "what's the status of my late checkout?", /still waiting/);

  const staff = await (await browser.newContext()).newPage();
  await staffLogin(staff, "reception@demo.hotel");
  await staff.waitForURL("**/staff");
  await staff.goto("/staff/approvals");
  const card = staff.locator("section", { hasText: code });
  await expect(card.getByText("not specified").or(card.getByText("2 pm"))).toBeVisible();
  await card.getByRole("button", { name: "Approve" }).click();
  await staff.getByLabel("Approved until (optional)").fill("2:00 PM");
  await staff.getByRole("dialog").getByRole("button", { name: "Approve" }).click();
  await expect(staff.getByRole("dialog")).toBeHidden();
  await say(guest, "status of my late checkout", /approved until 2:00 PM/);
});

test("handoff: the conversation shows up for staff and their reply reaches the guest", async ({ browser }) => {
  const guest = await (await browser.newContext()).newPage();
  await openChat(guest);
  await say(guest, "What time is check-out?", /11:00 AM/);
  await say(guest, "I want to speak to a manager about my booking", /passed your conversation to our team/);

  const staff = await (await browser.newContext()).newPage();
  await staffLogin(staff, "reception@demo.hotel");
  await staff.waitForURL("**/staff");
  await expect(staff.getByRole("button", { name: /Notifications, [1-9]\d* unread/ })).toBeVisible({ timeout: 25_000 });
  await staff.goto("/staff/conversations");
  await staff.getByLabel("Status").selectOption("handed_off");
  await staff.getByRole("list", { name: "Conversations" }).getByRole("button").first().click();
  await expect(staff.getByRole("log", { name: "Messages" }).getByText(/speak to a manager/)).toBeVisible();
  await staff.getByLabel("Reply to guest").fill("Hello, this is Riley from the front desk.");
  await staff.getByRole("button", { name: "Send", exact: true }).click();
  await expect(guest.getByRole("log").getByText("Hello, this is Riley from the front desk.")).toBeVisible({ timeout: 20_000 });
});
