"use client";

import { FormEvent, useEffect, useState } from "react";
import PageHeader from "@/components/staff/PageHeader";
import { useStaff } from "@/components/staff/context";
import { Badge, Button, ErrorState, Field, Input, Modal, Notice, Select, State, Table } from "@/components/ui";
import { ApiError, staff } from "@/lib/api";
import { dt, label } from "@/lib/format";
import { useAsync } from "@/lib/hooks";
import { AdminUser, Department } from "@/lib/types";

const ROLES = ["admin", "manager", "receptionist", "department_staff"];
type Assignment = { hotel_id: string; department_id: string | null };

export default function TeamPage() {
  const { me, hotels, can } = useStaff();
  const users = useAsync(() => staff<AdminUser[]>("users"), []);
  const [editing, setEditing] = useState<AdminUser | "new" | null>(null);
  const [resetting, setResetting] = useState<AdminUser | null>(null);
  const [msg, setMsg] = useState<{ tone: "success" | "error"; text: string } | null>(null);

  if (!can("admin")) return <State kind="denied" detail="Only administrators can manage the team." />;
  const done = (text: string) => { setMsg({ tone: "success", text }); setEditing(null); setResetting(null); users.reload(); };

  return (
    <>
      <PageHeader title="Team" subtitle="Staff accounts, roles and hotel access"
        actions={<Button onClick={() => { setMsg(null); setEditing("new"); }}>Add staff member</Button>} />
      {msg && <div className="mb-4"><Notice tone={msg.tone}>{msg.text}</Notice></div>}
      {users.loading ? <State kind="loading" /> : users.error ? <ErrorState error={users.error} retry={users.reload} /> : !users.data?.length ? <State kind="empty" /> : (
        <Table caption="Staff accounts" head={["Name", "Role", "Access", "Last sign-in", "Status", "Actions"]}>
          {users.data.map((u) => (
            <tr key={u.id}>
              <td className="px-3 py-2"><p className="font-medium text-slate-900">{u.full_name}{u.id === me.id && <span className="ml-1 text-xs font-normal text-slate-500">(you)</span>}</p><p className="text-xs text-slate-500">{u.email}</p></td>
              <td className="px-3 py-2">{label(u.role)}</td>
              <td className="px-3 py-2 text-xs text-slate-700">{u.assignments.length ? u.assignments.map((a) => `${a.hotel_name}${a.department_name ? ` · ${a.department_name}` : ""}`).join("; ") : "No hotels"}</td>
              <td className="whitespace-nowrap px-3 py-2">{dt(u.last_login_at)}</td>
              <td className="px-3 py-2"><Badge tone={u.is_active ? "green" : "slate"}>{u.is_active ? "Active" : "Disabled"}</Badge></td>
              <td className="px-3 py-2"><div className="flex gap-1.5">
                <Button size="sm" variant="secondary" onClick={() => { setMsg(null); setEditing(u); }} aria-label={`Edit ${u.full_name}`}>Edit</Button>
                <Button size="sm" variant="secondary" onClick={() => { setMsg(null); setResetting(u); }} aria-label={`Reset password for ${u.full_name}`}>Reset password</Button>
              </div></td>
            </tr>))}
        </Table>)}
      {editing && <UserForm user={editing === "new" ? null : editing} selfId={me.id} hotels={hotels} onClose={() => setEditing(null)} onDone={done} />}
      {resetting && <ResetForm user={resetting} onClose={() => setResetting(null)} onDone={done} />}
    </>
  );
}

function Assignments({ hotels, value, onChange }: {
  hotels: { id: string; name: string }[]; value: Assignment[]; onChange: (a: Assignment[]) => void;
}) {
  const [depts, setDepts] = useState<Record<string, Department[]>>({});
  useEffect(() => {
    hotels.forEach((h) => staff<Department[]>(`hotels/${h.id}/departments`).then((d) => setDepts((p) => ({ ...p, [h.id]: d }))).catch(() => {}));
  }, [hotels]);
  const find = (id: string) => value.find((a) => a.hotel_id === id);
  return (
    <fieldset className="space-y-2">
      <legend className="text-sm font-medium text-slate-700">Hotel access</legend>
      {hotels.map((h) => {
        const a = find(h.id);
        return (
          <div key={h.id} className="flex flex-wrap items-center gap-3 rounded-md border border-slate-200 p-2">
            <label className="flex items-center gap-2 text-sm text-slate-800">
              <input type="checkbox" className="h-4 w-4 rounded border-slate-300" checked={!!a}
                onChange={(e) => onChange(e.target.checked ? [...value, { hotel_id: h.id, department_id: null }] : value.filter((x) => x.hotel_id !== h.id))} />
              {h.name}
            </label>
            {a && (
              <Select aria-label={`Department at ${h.name}`} className="w-48" value={a.department_id ?? ""}
                onChange={(e) => onChange(value.map((x) => x.hotel_id === h.id ? { ...x, department_id: e.target.value || null } : x))}>
                <option value="">All departments</option>
                {(depts[h.id] ?? []).map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
              </Select>)}
          </div>);
      })}
    </fieldset>
  );
}

function UserForm({ user, selfId, hotels, onClose, onDone }: {
  user: AdminUser | null; selfId: string; hotels: { id: string; name: string }[];
  onClose: () => void; onDone: (text: string) => void;
}) {
  const [assign, setAssign] = useState<Assignment[]>(user?.assignments.map((a) => ({ hotel_id: a.hotel_id, department_id: a.department_id })) ?? []);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const self = user?.id === selfId;
  const submit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    setBusy(true); setErr(null);
    try {
      if (!user) {
        await staff("users", { method: "POST", body: { email: f.get("email"), full_name: f.get("full_name"), role: f.get("role"), password: f.get("password"), assignments: assign } });
        onDone(`Added ${f.get("full_name")}.`);
      } else {
        const body: Record<string, unknown> = { full_name: f.get("full_name"), assignments: assign };
        if (!self) { body.role = f.get("role"); body.is_active = f.get("is_active") === "on"; }
        await staff(`users/${user.id}`, { method: "PATCH", body });
        onDone(`Saved ${f.get("full_name")}.${!self ? " Role or status changes sign the person out everywhere." : ""}`);
      }
    } catch (x) { setErr(x instanceof ApiError ? x.message : "Could not save"); } finally { setBusy(false); }
  };
  return (
    <Modal title={user ? `Edit ${user.full_name}` : "Add staff member"} onClose={onClose}>
      <form onSubmit={submit} className="space-y-3">
        {err && <Notice tone="error">{err}</Notice>}
        <Field label="Full name" htmlFor="u-name"><Input id="u-name" name="full_name" required maxLength={200} defaultValue={user?.full_name ?? ""} /></Field>
        {!user && <Field label="Email" htmlFor="u-email"><Input id="u-email" name="email" type="email" required autoComplete="off" /></Field>}
        <Field label="Role" htmlFor="u-role" hint={self ? "You cannot change your own role." : undefined}>
          <Select id="u-role" name="role" defaultValue={user?.role ?? "receptionist"} disabled={self}>{ROLES.map((r) => <option key={r} value={r}>{label(r)}</option>)}</Select>
        </Field>
        {!user && <Field label="Temporary password" htmlFor="u-pw" hint="At least 12 characters; not based on the name or email. Ask them to change it after signing in."><Input id="u-pw" name="password" type="password" required autoComplete="new-password" /></Field>}
        {user && !self && (
          <label className="flex items-center gap-2 text-sm text-slate-800"><input type="checkbox" name="is_active" defaultChecked={user.is_active} className="h-4 w-4 rounded border-slate-300" /> Account active (disabling signs the person out immediately)</label>)}
        <Assignments hotels={hotels} value={assign} onChange={setAssign} />
        <div className="flex justify-end gap-2 pt-1"><Button type="button" variant="secondary" onClick={onClose}>Cancel</Button><Button type="submit" disabled={busy}>{busy ? "Saving…" : "Save"}</Button></div>
      </form>
    </Modal>
  );
}

function ResetForm({ user, onClose, onDone }: { user: AdminUser; onClose: () => void; onDone: (t: string) => void }) {
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const pw = String(new FormData(e.currentTarget).get("new_password") ?? "");
    setBusy(true); setErr(null);
    try { await staff(`users/${user.id}/reset-password`, { method: "POST", body: { new_password: pw } }); onDone(`Password reset for ${user.full_name}. Their existing sessions were ended.`); }
    catch (x) { setErr(x instanceof ApiError ? x.message : "Could not reset the password"); } finally { setBusy(false); }
  };
  return (
    <Modal title={`Reset password for ${user.full_name}`} onClose={onClose}>
      <form onSubmit={submit} className="space-y-3">
        {err && <Notice tone="error">{err}</Notice>}
        <Field label="New temporary password" htmlFor="rp-pw" hint="Share it through a secure channel. It ends all of this person's sessions."><Input id="rp-pw" name="new_password" type="password" required autoComplete="new-password" /></Field>
        <div className="flex justify-end gap-2"><Button type="button" variant="secondary" onClick={onClose}>Cancel</Button><Button type="submit" variant="danger" disabled={busy}>{busy ? "Resetting…" : "Reset password"}</Button></div>
      </form>
    </Modal>
  );
}
