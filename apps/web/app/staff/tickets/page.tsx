"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import PageHeader from "@/components/staff/PageHeader";
import { FRONT_OFFICE, useStaff } from "@/components/staff/context";
import { Badge, Button, ErrorState, Field, Input, Modal, Notice, Select, State, Table, Textarea, priorityTone, statusTone } from "@/components/ui";
import { ApiError, qs, staff } from "@/lib/api";
import { dt, label, TRANSITIONS } from "@/lib/format";
import { useAsync, useDebounced } from "@/lib/hooks";
import { Department, HistoryEvent, StaffMember, Ticket } from "@/lib/types";

function Detail({ id, onClose, onChanged }: { id: string; onClose: () => void; onChanged: () => void }) {
  const { hotel, can } = useStaff();
  const base = `hotels/${hotel.id}`;
  const t = useAsync(() => staff<Ticket>(`${base}/tickets/${id}`), [id]);
  const hist = useAsync(() => staff<HistoryEvent[]>(`${base}/tickets/${id}/history`), [id]);
  const people = useAsync(() => can(...FRONT_OFFICE) ? staff<StaffMember[]>(`${base}/staff`) : Promise.resolve([]), [id]);
  const conv = useAsync(() => can(...FRONT_OFFICE) ? staff<{ conversation_id: string | null }>(`${base}/tickets/${id}/conversation`) : Promise.resolve(null), [id]);
  const [msg, setMsg] = useState<{ tone: "success" | "error"; text: string } | null>(null);
  const [note, setNote] = useState("");
  const act = async (fn: () => Promise<unknown>, ok: string) => {
    try { await fn(); setMsg({ tone: "success", text: ok }); t.reload(); hist.reload(); onChanged(); }
    catch (e) { setMsg({ tone: "error", text: e instanceof ApiError ? e.message : "Action failed" }); }
  };
  return (
    <Modal title={t.data ? `Ticket ${t.data.ticket_number}` : "Ticket"} onClose={onClose} wide>
      {t.loading ? <State kind="loading" /> : t.error ? <ErrorState error={t.error} retry={t.reload} /> : t.data && (
        <div className="space-y-5">
          {msg && <Notice tone={msg.tone}>{msg.text}</Notice>}
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={statusTone(t.data.status)}>{label(t.data.status)}</Badge>
            <Badge tone={priorityTone(t.data.priority)}>{t.data.priority}</Badge>
            <Badge>{label(t.data.category)}</Badge>
            {t.data.room_number && <Badge>Room {t.data.room_number}</Badge>}
            <span className="text-xs text-slate-500">Due {dt(t.data.due_at)}</span>
          </div>
          <p className="whitespace-pre-wrap rounded-md bg-slate-50 p-3 text-sm text-slate-800">{t.data.description}</p>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Change status" htmlFor="status">
              <div className="flex flex-wrap gap-2" id="status">
                {(TRANSITIONS[t.data.status] ?? []).length === 0 && <span className="text-sm text-slate-500">No further transitions.</span>}
                {(TRANSITIONS[t.data.status] ?? []).map((s) => (
                  <Button key={s} size="sm" variant={s === "escalated" ? "danger" : "secondary"} onClick={() => act(() => staff(`${base}/tickets/${id}/status`, { method: "POST", body: { status: s } }), `Status changed to ${label(s)}`)}>{label(s)}</Button>))}
              </div>
            </Field>
            {can(...FRONT_OFFICE) && (
              <Field label="Assign to" htmlFor="assignee">
                <Select id="assignee" value={t.data.assigned_to ?? ""} onChange={(e) => e.target.value && act(() => staff(`${base}/tickets/${id}/assign`, { method: "POST", body: { assignee_id: e.target.value } }), "Ticket assigned")}>
                  <option value="">Unassigned</option>
                  {people.data?.map((p) => <option key={p.id} value={p.id}>{p.full_name} ({label(p.role)})</option>)}
                </Select>
              </Field>
            )}
          </div>
          <Field label="Internal notes" htmlFor="note" hint="Visible to staff only.">
            <ul className="mb-2 space-y-1 text-sm">{t.data.internal_notes.map((n, i) => <li key={i} className="rounded bg-amber-50 px-2 py-1 text-amber-950"><span className="text-xs text-amber-800">{dt(n.at)}</span><br />{n.text}</li>)}</ul>
            <div className="flex gap-2"><Textarea id="note" rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
              <Button disabled={!note.trim()} onClick={() => act(async () => { await staff(`${base}/tickets/${id}/notes`, { method: "POST", body: { note } }); setNote(""); }, "Note added")}>Add</Button></div>
          </Field>
          {conv.data?.conversation_id && <p className="text-sm"><Link className="font-medium text-teal-800 underline" href={`/staff/conversations?open=${conv.data.conversation_id}`}>View the guest conversation</Link></p>}
          <div>
            <h3 className="mb-2 text-sm font-semibold text-slate-900">History</h3>
            {hist.loading ? <State kind="loading" /> : hist.error ? <ErrorState error={hist.error} /> : (
              <ol className="space-y-1.5 border-l-2 border-slate-200 pl-3 text-sm">{hist.data?.map((h, i) => (
                <li key={i}><span className="font-medium">{label(h.action.split(".").pop() ?? h.action)}</span> <span className="text-slate-500">· {h.actor_name ?? label(h.actor_type)} · {dt(h.created_at)}</span>
                  {h.action === "ticket.escalated" && <span className="block text-xs text-red-700">Reason: {String(h.details.reason ?? "—")}</span>}</li>))}</ol>)}
          </div>
        </div>
      )}
    </Modal>
  );
}

function Tickets() {
  const { hotel, can } = useStaff();
  const params = useSearchParams();
  const [q, setQ] = useState("");
  const [f, setF] = useState({ status: "", priority: "", department_id: "", assigned_to: "", created_from: "", created_to: "", overdue: params.get("overdue") === "1" });
  const [open, setOpen] = useState<string | null>(params.get("open"));
  const dq = useDebounced(q);
  const base = `hotels/${hotel.id}`;
  const depts = useAsync(() => staff<Department[]>(`${base}/departments`), [hotel.id]);
  const people = useAsync(() => can(...FRONT_OFFICE) ? staff<StaffMember[]>(`${base}/staff`) : Promise.resolve([]), [hotel.id]);
  const list = useAsync(() => staff<Ticket[]>(`${base}/tickets-search${qs({ q: dq, ...f, limit: 100 })}`), [hotel.id, dq, JSON.stringify(f)]);
  useEffect(() => { if (open) window.history.replaceState(null, "", "/staff/tickets"); }, [open]);
  const set = (k: string) => (e: React.ChangeEvent<HTMLSelectElement | HTMLInputElement>) => setF({ ...f, [k]: e.target.type === "checkbox" ? (e.target as HTMLInputElement).checked : e.target.value });
  return (
    <>
      <PageHeader title="Tickets" subtitle="Service requests, complaints and escalations" />
      <div className="mb-4 grid gap-3 rounded-lg border border-slate-200 bg-white p-3 sm:grid-cols-2 lg:grid-cols-4">
        <Field label="Search" htmlFor="q"><Input id="q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ticket no., room, or text" /></Field>
        <Field label="Status" htmlFor="st"><Select id="st" value={f.status} onChange={set("status")}><option value="">All</option>{Object.keys(TRANSITIONS).map((s) => <option key={s} value={s}>{label(s)}</option>)}</Select></Field>
        <Field label="Priority" htmlFor="pr"><Select id="pr" value={f.priority} onChange={set("priority")}><option value="">All</option>{["low", "normal", "high", "urgent"].map((s) => <option key={s} value={s}>{label(s)}</option>)}</Select></Field>
        <Field label="Department" htmlFor="dp"><Select id="dp" value={f.department_id} onChange={set("department_id")}><option value="">All</option>{depts.data?.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}</Select></Field>
        {can(...FRONT_OFFICE) && <Field label="Assignee" htmlFor="as"><Select id="as" value={f.assigned_to} onChange={set("assigned_to")}><option value="">Anyone</option>{people.data?.map((p) => <option key={p.id} value={p.id}>{p.full_name}</option>)}</Select></Field>}
        <Field label="Created from" htmlFor="cf"><Input id="cf" type="date" value={f.created_from} onChange={set("created_from")} /></Field>
        <Field label="Created to" htmlFor="ct"><Input id="ct" type="date" value={f.created_to} onChange={set("created_to")} /></Field>
        <label className="flex items-end gap-2 pb-2 text-sm"><input type="checkbox" checked={f.overdue} onChange={set("overdue")} /> Overdue only</label>
      </div>
      {list.loading ? <State kind="loading" /> : list.error ? <ErrorState error={list.error} retry={list.reload} /> : !list.data?.length ? <State kind="empty" title="No tickets found" detail="Try changing or clearing the filters." /> : (
        <Table caption="Tickets" head={["Ticket", "Priority", "Status", "Category", "Room", "Department", "Assignee", "Created"]}>
          {list.data.map((t) => (
            <tr key={t.id} className="hover:bg-slate-50">
              <td className="px-3 py-2"><button className="font-medium text-teal-800 underline" onClick={() => setOpen(t.id)} aria-label={`Open ticket ${t.ticket_number}`}>{t.ticket_number}</button><p className="max-w-xs truncate text-xs text-slate-500">{t.description}</p></td>
              <td className="px-3 py-2"><Badge tone={priorityTone(t.priority)}>{t.priority}</Badge></td>
              <td className="px-3 py-2"><Badge tone={statusTone(t.status)}>{label(t.status)}</Badge></td>
              <td className="px-3 py-2">{label(t.category)}</td><td className="px-3 py-2">{t.room_number ?? "—"}</td>
              <td className="px-3 py-2">{t.department_name ?? "—"}</td><td className="px-3 py-2">{t.assignee_name ?? "—"}</td>
              <td className="whitespace-nowrap px-3 py-2 text-slate-500">{dt(t.created_at)}</td>
            </tr>))}
        </Table>)}
      {open && <Detail id={open} onClose={() => setOpen(null)} onChanged={list.reload} />}
    </>
  );
}

export default function Page() { return <Suspense><Tickets /></Suspense>; }
