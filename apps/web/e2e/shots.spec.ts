// On-demand visual capture (not part of the normal run): SHOTS=1 ./e2e/run.sh e2e/shots.spec.ts
import { test } from "@playwright/test";
import { checkedInStay, iso, openChat, say, staffLogin } from "./helpers";

test.skip(!process.env.SHOTS, "set SHOTS=1 to capture screenshots");

test("capture", async ({ browser, request }) => {
  const dir = "reports/shots";
  const code = await checkedInStay(request, "Alex Mercer");
  const g = await (await browser.newContext({ viewport: { width: 420, height: 800 } })).newPage();
  await openChat(g);
  await say(g, "What time is breakfast on weekends?", /7:00 AM/);
  await g.getByRole("button", { name: "Verify my stay" }).click();
  await g.getByLabel("Confirmation code").fill(code);
  await g.getByLabel("Last name").fill("Mercer");
  await g.getByRole("button", { name: "Verify", exact: true }).click();
  await say(g, "The AC in my room is broken and it is very hot", /logged a maintenance request/);
  await say(g, "Could I have a late checkout until 2 pm please?", /NOT confirmed/);
  await g.screenshot({ path: `${dir}/guest-chat.png` });
  const g2 = await (await browser.newContext()).newPage();
  await openChat(g2);
  await say(g2, "The staff were rude and I want a refund", /recorded your complaint/);
  await say(g2, "I'd like to book a room", /dates/);
  await say(g2, `${iso(30)} to ${iso(32)} for 2 adults`, /Deluxe King/);
  await say(g2, "Deluxe King", /name/);
  await say(g2, "Sam Shots", /Reply YES/);
  await g2.screenshot({ path: `${dir}/guest-confirm.png` });

  const s = await (await browser.newContext({ viewport: { width: 1280, height: 900 } })).newPage();
  await s.goto("/");
  await s.screenshot({ path: `${dir}/landing.png` });
  await staffLogin(s, "manager@demo.hotel");
  await s.waitForURL("**/staff");
  await s.getByText("Open requests", { exact: true }).waitFor();
  await s.screenshot({ path: `${dir}/overview.png`, fullPage: true });
  await s.goto("/staff/tickets");
  await s.getByRole("table").waitFor();
  await s.screenshot({ path: `${dir}/tickets.png` });
  await s.getByRole("button", { name: /Open ticket/ }).first().click();
  await s.getByRole("dialog").waitFor();
  await s.screenshot({ path: `${dir}/ticket-detail.png` });
  await s.keyboard.press("Escape");
  await s.goto("/staff/reservations");
  await s.getByRole("table").waitFor();
  await s.screenshot({ path: `${dir}/reservations.png` });
  await s.goto("/staff/approvals");
  await s.getByText("Late checkout").first().waitFor();
  await s.screenshot({ path: `${dir}/approvals.png` });
  await s.goto("/login");
  await s.screenshot({ path: `${dir}/login.png` });
});
