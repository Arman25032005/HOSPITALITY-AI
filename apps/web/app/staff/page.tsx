"use client";

import Link from "next/link";
import { useState } from "react";
import PageHeader from "@/components/staff/PageHeader";
import { MANAGERS, useStaff } from "@/components/staff/context";
import { Badge, Card, ErrorState, Select, State, priorityTone, statusTone } from "@/components/ui";
import { qs, staff } from "@/lib/api";
import { label, today } from "@/lib/format";
import { useAsync } from "@/lib/hooks";
import { Analytics, Approval, Reservation, Ticket } from "@/lib/types";

function Tile({ name, value, hint, tone }: { name: string; value: string | number; hint?: string; tone?: "red" }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{name}</p>
      <p className={`mt-1 text-2xl font-semibold ${tone === "red" ? "text-red-700" : "text-slate-900"}`}>{value}</p>
      {hint && <p className="mt-0.5 text-xs text-slate-500">{hint}</p>}
    </div>
  );
}

function Bars({ data, caption }: { data: Record<string, number>; caption: string }) {
  const entries = Object.entries(data);
  const max = Math.max(1, ...entries.map(([, v]) => v));
  if (!entries.length) return <p className="text-sm text-slate-500">No open requests.</p>;
  return (
    <ul aria-label={caption} className="space-y-2">
      {entries.map(([k, v]) => (
        <li key={k} className="flex items-center gap-3 text-sm">
          <span className="w-32 shrink-0 truncate text-slate-700">{label(k)}</span>
          <span className="h-2.5 flex-1 rounded-full bg-slate-100"><span className="block h-2.5 rounded-full bg-teal-600" style={{ width: `${(v / max) * 100}%` }} /></span>
          <span className="w-6 text-right font-medium tabular-nums">{v}</span>
        </li>
      ))}
    </ul>
  );
}

function Spark({ points, caption }: { points: { date: string; count: number }[]; caption: string }) {
  if (points.length < 2) return <p className="text-sm text-slate-500">Not enough data for a trend yet.</p>;
  const max = Math.max(1, ...points.map((p) => p.count));
  const w = 240, h = 48;
  const d = points.map((p, i) => `${i ? "L" : "M"}${(i / (points.length - 1)) * w},${h - (p.count / max) * (h - 6) - 3}`).join(" ");
  return (
    <figure>
      <svg viewBox={`0 0 ${w} ${h}`} role="img" aria-label={caption} className="h-12 w-full"><path d={d} fill="none" stroke="#0f766e" strokeWidth="2" /></svg>
      <figcaption className="sr-only">{points.map((p) => `${p.date}: ${p.count}`).join(", ")}</figcaption>
    </figure>
  );
}

function Attention({ hotelId }: { hotelId: string }) {
  const { can } = useStaff();
  const urgent = useAsync(() => staff<Ticket[]>(`hotels/${hotelId}/tickets-search${qs({ overdue: true, limit: 5 })}`), [hotelId]);
  const esc = useAsync(() => staff<Ticket[]>(`hotels/${hotelId}/tickets-search${qs({ status: "escalated", limit: 5 })}`), [hotelId]);
  const appr = useAsync(() => can("admin", "manager", "receptionist") ? staff<Approval[]>(`hotels/${hotelId}/approvals?status=pending`) : Promise.resolve([]), [hotelId]);
  const arrivals = useAsync(() => can("admin", "manager", "receptionist") ? staff<Reservation[]>(`hotels/${hotelId}/reservations${qs({ arrival_from: today(), arrival_to: today(), status: "confirmed" })}`) : Promise.resolve([]), [hotelId]);
  const list = (rows: Ticket[] | null) => rows?.length ? (
    <ul className="divide-y divide-slate-100">{rows.map((t) => (
      <li key={t.id} className="flex items-center gap-2 py-2 text-sm">
        <Link href={`/staff/tickets?open=${t.id}`} className="font-medium text-teal-800 underline">{t.ticket_number}</Link>
        <Badge tone={priorityTone(t.priority)}>{t.priority}</Badge>
        <span className="min-w-0 flex-1 truncate text-slate-600">{t.description}</span>
      </li>))}</ul>
  ) : <p className="text-sm text-slate-500">Nothing to show.</p>;
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title="Overdue tickets" actions={<Link className="text-sm text-teal-800 underline" href="/staff/tickets?overdue=1">View all</Link>}>{list(urgent.data)}</Card>
      <Card title="Escalated tickets">{list(esc.data)}</Card>
      {can("admin", "manager", "receptionist") && <>
        <Card title="Awaiting approval" actions={<Link className="text-sm text-teal-800 underline" href="/staff/approvals">Review</Link>}>
          <p className="text-2xl font-semibold">{appr.data?.length ?? "—"}</p><p className="text-sm text-slate-500">pending requests</p>
        </Card>
        <Card title="Arrivals today" actions={<Link className="text-sm text-teal-800 underline" href="/staff/reservations">Reservations</Link>}>
          <p className="text-2xl font-semibold">{arrivals.data?.length ?? "—"}</p><p className="text-sm text-slate-500">confirmed reservations arriving</p>
        </Card>
      </>}
    </div>
  );
}

export default function Overview() {
  const { hotel, can, me } = useStaff();
  const [days, setDays] = useState(7);
  const a = useAsync(() => can(...MANAGERS) ? staff<Analytics>(`hotels/${hotel.id}/analytics/summary?days=${days}`) : Promise.resolve(null), [hotel.id, days]);
  const full = can(...MANAGERS);
  return (
    <>
      <PageHeader title="Overview" subtitle={`${hotel.name} · signed in as ${label(me.role)}`}
        actions={full && <Select aria-label="Time window" value={days} onChange={(e) => setDays(Number(e.target.value))} className="w-40"><option value={1}>Last 24 hours</option><option value={7}>Last 7 days</option><option value={30}>Last 30 days</option></Select>} />
      {full && (a.loading ? <State kind="loading" /> : a.error ? <ErrorState error={a.error} retry={a.reload} /> : a.data && (
        <div className="mb-6 space-y-4">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
            <Tile name="Open requests" value={a.data.open_requests} hint="right now" />
            <Tile name="Overdue" value={a.data.overdue} tone={a.data.overdue ? "red" : undefined} hint="past due date" />
            <Tile name="Avg resolution" value={a.data.avg_resolution_hours === null ? "—" : `${a.data.avg_resolution_hours} h`} hint={`resolved, last ${a.data.window_days}d`} />
            <Tile name="Escalations" value={a.data.escalations_in_window} hint={`last ${a.data.window_days}d`} />
            <Tile name="Active stays" value={a.data.active_reservations} hint="today" />
            <Tile name="Guest rating" value={a.data.avg_feedback_rating ?? "—"} hint="average, 1–5" />
          </div>
          <div className="grid gap-4 lg:grid-cols-3">
            <Card title="Department workload (open)"><Bars data={a.data.open_by_department} caption="Open requests by department" /></Card>
            <Card title="Tickets created per day"><Spark points={a.data.tickets_per_day} caption="Tickets created per day" /></Card>
            <Card title="Complaint themes"><Bars data={Object.fromEntries(Object.entries(a.data.complaint_themes).filter(([, v]) => v))} caption="Complaints by theme" /></Card>
          </div>
          {a.data.feedback_trend.length > 0 && (
            <Card title="Guest feedback by day"><ul className="flex flex-wrap gap-2 text-sm">{a.data.feedback_trend.map((f) => <li key={f.date}><Badge tone={f.avg_rating >= 4 ? "green" : f.avg_rating >= 3 ? "amber" : "red"}>{f.date}: {f.avg_rating} ({f.count})</Badge></li>)}</ul></Card>
          )}
        </div>
      ))}
      {!full && <p className="mb-4 text-sm text-slate-500">Detailed analytics are available to managers and administrators.</p>}
      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">Needs attention</h2>
      <Attention hotelId={hotel.id} />
    </>
  );
}
