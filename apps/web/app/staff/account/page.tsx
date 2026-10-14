"use client";

import { FormEvent, useState } from "react";
import PageHeader from "@/components/staff/PageHeader";
import { useStaff } from "@/components/staff/context";
import { Badge, Button, Card, Field, Input, Notice } from "@/components/ui";
import { label } from "@/lib/format";

export default function AccountPage() {
  const { me } = useStaff();
  const [msg, setMsg] = useState<{ tone: "success" | "error"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = e.currentTarget;
    const f = new FormData(form);
    if (f.get("new_password") !== f.get("confirm")) { setMsg({ tone: "error", text: "The new passwords do not match." }); return; }
    setBusy(true); setMsg(null);
    try {
      const r = await fetch("/api/auth/change-password", {
        method: "POST", cache: "no-store",
        headers: { "content-type": "application/json", "x-requested-with": "hospitality" },
        body: JSON.stringify({ current_password: f.get("current_password"), new_password: f.get("new_password") }),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) setMsg({ tone: "error", text: typeof data.detail === "string" ? data.detail : "Could not change the password" });
      else { setMsg({ tone: "success", text: "Password changed. Your other sessions were signed out." }); form.reset(); }
    } catch { setMsg({ tone: "error", text: "The service is temporarily unavailable" }); } finally { setBusy(false); }
  };
  return (
    <>
      <PageHeader title="My account" />
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Profile">
          <dl className="space-y-2 text-sm">
            <div><dt className="text-slate-500">Name</dt><dd className="font-medium">{me.full_name}</dd></div>
            <div><dt className="text-slate-500">Email</dt><dd>{me.email}</dd></div>
            <div><dt className="text-slate-500">Role</dt><dd><Badge>{label(me.role)}</Badge></dd></div>
          </dl>
        </Card>
        <Card title="Change password">
          <form onSubmit={submit} className="space-y-3" aria-label="Change password">
            {msg && <Notice tone={msg.tone}>{msg.text}</Notice>}
            <Field label="Current password" htmlFor="cp-cur"><Input id="cp-cur" name="current_password" type="password" required autoComplete="current-password" /></Field>
            <Field label="New password" htmlFor="cp-new" hint="At least 12 characters; not based on your name or email."><Input id="cp-new" name="new_password" type="password" required autoComplete="new-password" /></Field>
            <Field label="Confirm new password" htmlFor="cp-conf"><Input id="cp-conf" name="confirm" type="password" required autoComplete="new-password" /></Field>
            <Button type="submit" disabled={busy}>{busy ? "Saving…" : "Change password"}</Button>
          </form>
        </Card>
      </div>
    </>
  );
}
