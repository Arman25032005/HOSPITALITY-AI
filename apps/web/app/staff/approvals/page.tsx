"use client";

import { FormEvent, useState } from "react";
import PageHeader from "@/components/staff/PageHeader";
import { useStaff } from "@/components/staff/context";
import { Badge, Button, Card, ErrorState, Field, Input, Modal, Notice, Select, State, statusTone } from "@/components/ui";
import { ApiError, qs, staff } from "@/lib/api";
import { dt, label } from "@/lib/format";
import { useAsync } from "@/lib/hooks";
import { Approval } from "@/lib/types";

export default function ApprovalsPage() {
  const { hotel } = useStaff();
  const [status, setStatus] = useState("pending");
  const [deciding, setDeciding] = useState<{ a: Approval; approve: boolean } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const list = useAsync(() => staff<Approval[]>(`hotels/${hotel.id}/approvals${qs({ status })}`), [hotel.id, status]);
  const submit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (!deciding) return;
    const f = new FormData(e.currentTarget);
    try {
      await staff(`hotels/${hotel.id}/approvals/${deciding.a.id}/decide`, { method: "POST", body: { approve: deciding.approve, note: f.get("note") || null, approved_until: f.get("until") || null } });
      setDeciding(null); setErr(null); list.reload();
    } catch (x) { setErr(x instanceof ApiError ? x.message : "Could not record the decision"); }
  };
  return (
    <>
      <PageHeader title="Approvals" subtitle="Requests that need a staff decision before the guest is told"
        actions={<Select aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)} className="w-40"><option value="pending">Pending</option><option value="">All</option><option value="approved">Approved</option><option value="denied">Denied</option></Select>} />
      {list.loading ? <State kind="loading" /> : list.error ? <ErrorState error={list.error} retry={list.reload} /> : !list.data?.length ? <State kind="empty" title="No approval requests" detail={status === "pending" ? "Nothing is waiting for a decision." : undefined} /> : (
        <div className="grid gap-4 lg:grid-cols-2">
          {list.data.map((a) => {
            const p = a.payload as Record<string, string | number | null>;
            const dec = (a.payload.decision ?? {}) as { note?: string; approved_until?: string };
            return (
              <Card key={a.id} title={<span className="flex items-center gap-2">{label(a.kind)} <Badge tone={statusTone(a.status)}>{label(a.status)}</Badge></span>}
                actions={a.status === "pending" && <div className="flex gap-2"><Button size="sm" onClick={() => setDeciding({ a, approve: true })}>Approve</Button><Button size="sm" variant="danger" onClick={() => setDeciding({ a, approve: false })}>Deny</Button></div>}>
                <dl className="space-y-1 text-sm">
                  <div><dt className="inline text-slate-500">Reservation: </dt><dd className="inline font-medium">{String(p.confirmation_code ?? "—")}</dd></div>
                  <div><dt className="inline text-slate-500">Check-out: </dt><dd className="inline">{String(p.check_out ?? "—")}</dd></div>
                  <div><dt className="inline text-slate-500">Guest asked for: </dt><dd className="inline">{String(p.requested_until ?? "not specified")}</dd></div>
                  <div><dt className="inline text-slate-500">Same room type arriving on check-out day: </dt><dd className="inline font-medium">{String(p.same_room_type_arrivals_on_checkout_day ?? "—")}</dd></div>
                  {p.policy_excerpt && <div className="mt-2 rounded bg-slate-50 p-2 text-xs text-slate-600">{String(p.policy_excerpt)}</div>}
                  {a.status !== "pending" && <div className="mt-2 text-xs text-slate-500">Decided {dt(a.decided_at)}{dec.approved_until ? ` · until ${dec.approved_until}` : ""}{dec.note ? ` · “${dec.note}”` : ""}</div>}
                </dl>
                <p className="mt-2 text-xs text-slate-600">Requested {dt(a.created_at)}</p>
              </Card>);
          })}
        </div>)}
      {deciding && (
        <Modal title={deciding.approve ? "Approve request" : "Deny request"} onClose={() => { setDeciding(null); setErr(null); }}>
          <form onSubmit={submit} className="space-y-3">
            <p className="text-sm text-slate-600">The guest will be told exactly what you record here, nothing more.</p>
            {deciding.approve && <Field label="Approved until (optional)" htmlFor="until"><Input id="until" name="until" placeholder="e.g. 2:00 PM" maxLength={40} /></Field>}
            <Field label="Note to the guest (optional)" htmlFor="note"><Input id="note" name="note" maxLength={500} /></Field>
            {err && <Notice tone="error">{err}</Notice>}
            <div className="flex justify-end gap-2"><Button type="button" variant="secondary" onClick={() => setDeciding(null)}>Cancel</Button><Button type="submit" variant={deciding.approve ? "primary" : "danger"}>{deciding.approve ? "Approve" : "Deny"}</Button></div>
          </form>
        </Modal>)}
    </>
  );
}
