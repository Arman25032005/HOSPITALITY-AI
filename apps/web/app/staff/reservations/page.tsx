"use client";

import { FormEvent, useState } from "react";
import PageHeader from "@/components/staff/PageHeader";
import { useStaff } from "@/components/staff/context";
import { Badge, Button, ErrorState, Field, Input, Modal, Notice, Select, State, Table, statusTone } from "@/components/ui";
import { ApiError, qs, staff } from "@/lib/api";
import { day, dt, iso, label, money, today } from "@/lib/format";
import { useAsync, useDebounced } from "@/lib/hooks";
import { HistoryEvent, InventoryRow, Reservation } from "@/lib/types";

function Inventory({ hotelId }: { hotelId: string }) {
  const from = today();
  const to = iso(new Date(Date.now() + 13 * 864e5));
  const inv = useAsync(() => staff<InventoryRow[]>(`hotels/${hotelId}/inventory${qs({ from, to })}`), [hotelId]);
  if (inv.loading) return <State kind="loading" />;
  if (inv.error) return <ErrorState error={inv.error} retry={inv.reload} />;
  return (
    <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
      <table className="min-w-full text-xs">
        <caption className="sr-only">Booked rooms out of total rooms per night, next 14 days</caption>
        <thead><tr className="bg-slate-50"><th scope="col" className="sticky left-0 bg-slate-50 px-3 py-2 text-left">Room type</th>{inv.data?.[0]?.nights.map((n) => <th key={n.night} scope="col" className="px-2 py-2 text-center font-medium text-slate-600">{n.night.slice(5)}</th>)}</tr></thead>
        <tbody>{inv.data?.map((r) => (
          <tr key={r.room_type_id} className="border-t border-slate-100">
            <th scope="row" className="sticky left-0 bg-white px-3 py-2 text-left font-medium">{r.room_type_name}</th>
            {r.nights.map((n) => { const full = n.booked >= n.total; return <td key={n.night} className={`px-2 py-2 text-center tabular-nums ${full ? "bg-red-50 font-semibold text-red-800" : n.booked ? "bg-amber-50 text-amber-900" : "text-slate-500"}`} title={`${n.booked} booked of ${n.total}`}>{n.booked}/{n.total}</td>; })}
          </tr>))}</tbody>
      </table>
    </div>
  );
}

function Detail({ r, onClose, onChanged }: { r: Reservation; onClose: () => void; onChanged: () => void }) {
  const { hotel } = useStaff();
  const base = `hotels/${hotel.id}/reservations/${r.id}`;
  const hist = useAsync(() => staff<HistoryEvent[]>(`${base}/history`), [r.id, r.status, r.check_in, r.check_out]);
  const [msg, setMsg] = useState<{ tone: "success" | "error"; text: string } | null>(null);
  const [mode, setMode] = useState<"view" | "change" | "cancel">("view");
  const [busy, setBusy] = useState(false);
  const canCheckIn = r.status === "confirmed" && r.check_in <= today() && today() < r.check_out;
  const run = async (fn: () => Promise<unknown>, ok: string) => {
    setBusy(true);
    try { await fn(); setMsg({ tone: "success", text: ok }); setMode("view"); onChanged(); hist.reload(); }
    catch (e) { setMsg({ tone: "error", text: e instanceof ApiError ? e.message : "Action failed" }); }
    finally { setBusy(false); }
  };
  const submitChange = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    run(() => staff(`${base}/change`, { method: "POST", body: { check_in: f.get("ci"), check_out: f.get("co"), adults: Number(f.get("ad")) } }), "Reservation updated");
  };
  return (
    <Modal title={`Reservation ${r.confirmation_code}`} onClose={onClose} wide>
      <div className="space-y-4">
        {msg && <Notice tone={msg.tone}>{msg.text}</Notice>}
        <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
          <div><dt className="text-xs text-slate-500">Guest</dt><dd className="font-medium">{r.guest.full_name}</dd></div>
          <div><dt className="text-xs text-slate-500">Status</dt><dd><Badge tone={statusTone(r.status)}>{label(r.status)}</Badge></dd></div>
          <div><dt className="text-xs text-slate-500">Stay</dt><dd>{day(r.check_in)} → {day(r.check_out)} · {r.adults} adult(s)</dd></div>
          <div><dt className="text-xs text-slate-500">Room</dt><dd>{r.room_type_name}{r.room_number ? ` · room ${r.room_number}` : " · not assigned"}</dd></div>
          <div><dt className="text-xs text-slate-500">Total</dt><dd>{money(r.total_price, hotel.currency)}</dd></div>
          <div><dt className="text-xs text-slate-500">Contact</dt><dd>{[r.guest.email, r.guest.phone].filter(Boolean).join(" · ") || "—"}</dd></div>
        </dl>
        {mode === "view" && (
          <div className="flex flex-wrap gap-2">
            {canCheckIn && <Button disabled={busy} onClick={() => run(() => staff(`${base}/check-in`, { method: "POST", body: {} }), "Guest checked in")}>Check in</Button>}
            {r.status === "confirmed" && <Button variant="secondary" onClick={() => setMode("change")}>Change dates</Button>}
            {r.status === "confirmed" && <Button variant="danger" onClick={() => setMode("cancel")}>Cancel reservation</Button>}
          </div>)}
        {mode === "change" && (
          <form onSubmit={submitChange} className="grid gap-3 rounded-md border border-slate-200 p-3 sm:grid-cols-4">
            <Field label="Check-in" htmlFor="ci"><Input id="ci" name="ci" type="date" required min={today()} defaultValue={r.check_in} /></Field>
            <Field label="Check-out" htmlFor="co"><Input id="co" name="co" type="date" required min={today()} defaultValue={r.check_out} /></Field>
            <Field label="Adults" htmlFor="ad"><Input id="ad" name="ad" type="number" min={1} max={10} required defaultValue={r.adults} /></Field>
            <div className="flex items-end gap-2"><Button type="submit" disabled={busy}>Save</Button><Button type="button" variant="ghost" onClick={() => setMode("view")}>Back</Button></div>
          </form>)}
        {mode === "cancel" && (
          <div className="rounded-md border border-red-200 bg-red-50 p-3">
            <p className="text-sm text-red-900">Cancel {r.confirmation_code} for {r.guest.full_name}? The rooms are released immediately. Fees under the cancellation policy are handled outside this system.</p>
            <div className="mt-3 flex gap-2"><Button variant="danger" disabled={busy} onClick={() => run(() => staff(`${base}/cancel`, { method: "POST" }), "Reservation cancelled")}>Yes, cancel it</Button><Button variant="secondary" onClick={() => setMode("view")}>Keep reservation</Button></div>
          </div>)}
        <div>
          <h3 className="mb-2 text-sm font-semibold">Booking history</h3>
          {hist.loading ? <State kind="loading" /> : hist.error ? <ErrorState error={hist.error} /> : (
            <ol className="space-y-1.5 border-l-2 border-slate-200 pl-3 text-sm">{hist.data?.map((h, i) => <li key={i}><span className="font-medium">{label(h.action.split(".").pop() ?? "")}</span> <span className="text-slate-500">· {h.actor_name ?? label(h.actor_type)} · {dt(h.created_at)}</span></li>)}</ol>)}
        </div>
      </div>
    </Modal>
  );
}

export default function ReservationsPage() {
  const { hotel } = useStaff();
  const [view, setView] = useState<"list" | "inventory">("list");
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [open, setOpen] = useState<Reservation | null>(null);
  const dq = useDebounced(q);
  const list = useAsync(() => staff<Reservation[]>(`hotels/${hotel.id}/reservations${qs({ q: dq, status, arrival_from: from, arrival_to: to, limit: 100 })}`), [hotel.id, dq, status, from, to]);
  return (
    <>
      <PageHeader title="Reservations" subtitle="Search, check in, change or cancel"
        actions={<div role="tablist" aria-label="View" className="inline-flex rounded-md border border-slate-300 bg-white p-0.5 text-sm">
          {(["list", "inventory"] as const).map((v) => <button key={v} role="tab" aria-selected={view === v} onClick={() => setView(v)} className={`rounded px-3 py-1 ${view === v ? "bg-teal-700 text-white" : "text-slate-700"}`}>{v === "list" ? "Reservations" : "Inventory"}</button>)}</div>} />
      {view === "inventory" ? <Inventory hotelId={hotel.id} /> : <>
        <div className="mb-4 grid gap-3 rounded-lg border border-slate-200 bg-white p-3 sm:grid-cols-2 lg:grid-cols-4">
          <Field label="Search" htmlFor="rq"><Input id="rq" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Confirmation code or guest name" /></Field>
          <Field label="Status" htmlFor="rs"><Select id="rs" value={status} onChange={(e) => setStatus(e.target.value)}><option value="">All</option>{["confirmed", "checked_in", "checked_out", "cancelled"].map((s) => <option key={s} value={s}>{label(s)}</option>)}</Select></Field>
          <Field label="Arriving from" htmlFor="rf"><Input id="rf" type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></Field>
          <Field label="Arriving to" htmlFor="rt"><Input id="rt" type="date" value={to} onChange={(e) => setTo(e.target.value)} /></Field>
        </div>
        {list.loading ? <State kind="loading" /> : list.error ? <ErrorState error={list.error} retry={list.reload} /> : !list.data?.length ? <State kind="empty" title="No reservations found" /> : (
          <Table caption="Reservations" head={["Code", "Guest", "Stay", "Room", "Status", "Total"]}>
            {list.data.map((r) => (
              <tr key={r.id} className="hover:bg-slate-50">
                <td className="px-3 py-2"><button className="font-medium text-teal-800 underline" onClick={() => setOpen(r)} aria-label={`Open reservation ${r.confirmation_code}`}>{r.confirmation_code}</button></td>
                <td className="px-3 py-2">{r.guest.full_name}</td>
                <td className="whitespace-nowrap px-3 py-2">{day(r.check_in)} → {day(r.check_out)}</td>
                <td className="px-3 py-2">{r.room_type_name}{r.room_number ? ` · ${r.room_number}` : ""}</td>
                <td className="px-3 py-2"><Badge tone={statusTone(r.status)}>{label(r.status)}</Badge></td>
                <td className="px-3 py-2 tabular-nums">{money(r.total_price, hotel.currency)}</td>
              </tr>))}
          </Table>)}
      </>}
      {open && <Detail r={open} onClose={() => setOpen(null)} onChanged={async () => { const fresh = await staff<Reservation[]>(`hotels/${hotel.id}/reservations${qs({ q: open.confirmation_code })}`); setOpen(fresh[0] ?? null); list.reload(); }} />}
    </>
  );
}
