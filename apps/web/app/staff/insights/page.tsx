"use client";

import { useState } from "react";
import PageHeader from "@/components/staff/PageHeader";
import { useStaff } from "@/components/staff/context";
import { Badge, Card, ErrorState, Field, Input, Modal, Select, State, Table } from "@/components/ui";
import { qs, staff } from "@/lib/api";
import { dt, label } from "@/lib/format";
import { useAsync } from "@/lib/hooks";
import { AiExecution, AiKpis, AuditRow, Flagged, ToolStat, Unanswered } from "@/lib/types";

const TABS = ["Overview", "Conversations", "Tools", "Unanswered", "Low-rated", "Audit log"] as const;
type Tab = (typeof TABS)[number];

const pct = (v: number | null | undefined) => (v == null ? "n/a" : `${Math.round(v * 100)}%`);
const ms = (v: number | null | undefined) => (v == null ? "n/a" : `${Math.round(v)} ms`);
const usd = (v: number | null | undefined) => (v == null ? "n/a" : `$${v < 1 ? v.toFixed(4) : v.toFixed(2)}`);
const outcomeTone = (o: string | null) =>
  o === "escalated" || o === "handoff" || o === "error" ? "red" : o === "abstained" || o === "fallback" ? "amber" : o === "action_done" || o === "answered" ? "green" : "slate";

export default function InsightsPage() {
  const [tab, setTab] = useState<Tab>("Overview");
  const [days, setDays] = useState(7);
  return (
    <>
      <PageHeader title="AI insights" subtitle="How the assistant is performing, with sanitized traces"
        actions={<Select aria-label="Time window" value={days} onChange={(e) => setDays(Number(e.target.value))} className="!w-40"><option value={1}>Last 24 hours</option><option value={7}>Last 7 days</option><option value={30}>Last 30 days</option><option value={90}>Last 90 days</option></Select>} />
      <div role="tablist" aria-label="Insight sections" className="mb-4 flex flex-wrap gap-1 border-b border-slate-200">
        {TABS.map((t) => (
          <button key={t} role="tab" id={`tab-${t}`} aria-selected={tab === t} aria-controls="insight-panel" onClick={() => setTab(t)}
            className={`-mb-px rounded-t-md border-b-2 px-3 py-2 text-sm font-medium focus:outline-none focus-visible:ring-2 focus-visible:ring-teal-600 ${tab === t ? "border-teal-700 text-teal-900" : "border-transparent text-slate-600 hover:text-slate-900"}`}>{t}</button>))}
      </div>
      <div role="tabpanel" id="insight-panel" aria-labelledby={`tab-${tab}`}>
        {tab === "Overview" && <Overview days={days} />}
        {tab === "Conversations" && <Executions days={days} />}
        {tab === "Tools" && <Tools days={days} />}
        {tab === "Unanswered" && <UnansweredList days={days} />}
        {tab === "Low-rated" && <LowRated days={days} />}
        {tab === "Audit log" && <Audit />}
      </div>
    </>
  );
}

function Stat({ name, value, hint }: { name: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{name}</p>
      <p className="mt-1 text-2xl font-semibold text-slate-900">{value}</p>
      {hint && <p className="mt-0.5 text-xs text-slate-500">{hint}</p>}
    </div>
  );
}

function Overview({ days }: { days: number }) {
  const { hotel } = useStaff();
  const k = useAsync(() => staff<AiKpis>(`hotels/${hotel.id}/ai/kpis${qs({ days })}`), [hotel.id, days]);
  if (k.loading) return <State kind="loading" />;
  if (k.error || !k.data) return <ErrorState error={k.error} retry={k.reload} />;
  const d = k.data;
  if (!d.turns) return <State kind="empty" title="No assistant activity in this window" detail="Metrics appear once guests start chatting." />;
  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat name="Conversations" value={String(d.conversations)} hint={`${d.turns} assistant turns`} />
        <Stat name="Resolved by the assistant" value={pct(d.resolution_rate)} hint={`${d.resolved_conversations} of ${d.conversations}`} />
        <Stat name="Handed to staff" value={pct(d.handoff_rate)} hint={`${d.handed_off_conversations} ${d.handed_off_conversations === 1 ? "conversation" : "conversations"}`} />
        <Stat name="Unanswered knowledge questions" value={pct(d.knowledge_abstain_rate)} hint="share of knowledge turns that abstained" />
        <Stat name="Tool success" value={pct(d.tool_success_rate)} hint={`${d.tool_failures} failures in ${d.tool_calls} calls`} />
        <Stat name="Reservation completion" value={pct(d.reservation_completion_rate)} hint={`${d.reservation_flows_started} flows started`} />
        <Stat name="Tickets resolved (agent-created)" value={pct(d.agent_ticket_resolution_rate)} hint={`${d.agent_tickets_created} created`} />
        <Stat name="Guest rating" value={d.avg_feedback_rating == null ? "n/a" : `${d.avg_feedback_rating} / 5`} hint={`${d.feedback_count} ratings, ${d.low_rated_answers} low`} />
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Latency and cost">
          <dl className="grid grid-cols-2 gap-3 text-sm">
            <div><dt className="text-slate-500">Turn latency p50</dt><dd className="font-medium">{ms(d.turn_latency_p50_ms)}</dd></div>
            <div><dt className="text-slate-500">Turn latency p95</dt><dd className="font-medium">{ms(d.turn_latency_p95_ms)}</dd></div>
            <div><dt className="text-slate-500">Retrieval (avg)</dt><dd className="font-medium">{ms(d.retrieval_avg_latency_ms)}</dd></div>
            <div><dt className="text-slate-500">Model call (avg)</dt><dd className="font-medium">{ms(d.model_latency_avg_ms)}</dd></div>
            <div><dt className="text-slate-500">Tokens in / out</dt><dd className="font-medium">{d.tokens_in.toLocaleString()} / {d.tokens_out.toLocaleString()}</dd></div>
            <div><dt className="text-slate-500">Estimated cost</dt><dd className="font-medium">{usd(d.estimated_cost_usd)} <span className="text-xs font-normal text-slate-500">({usd(d.estimated_cost_per_conversation_usd)} / conversation)</span></dd></div>
          </dl>
          <p className="mt-3 text-xs text-slate-500">Cost is an estimate from configured per-token prices, not an invoice. With no model configured it is zero.</p>
        </Card>
        <Card title="Turn outcomes">
          <ul className="space-y-1.5 text-sm" aria-label="Outcomes">
            {Object.entries(d.outcomes).sort((a, b) => b[1] - a[1]).map(([o, n]) => (
              <li key={o} className="flex items-center justify-between gap-2"><Badge tone={outcomeTone(o)}>{label(o)}</Badge><span className="font-medium">{n}</span></li>))}
          </ul>
        </Card>
      </div>
    </div>
  );
}

function Executions({ days }: { days: number }) {
  const { hotel } = useStaff();
  const [outcome, setOutcome] = useState("");
  const [open, setOpen] = useState<AiExecution | null>(null);
  const list = useAsync(() => staff<AiExecution[]>(`hotels/${hotel.id}/ai/executions${qs({ days, outcome, limit: 50 })}`), [hotel.id, days, outcome]);
  return (
    <div className="space-y-3">
      <div className="max-w-xs"><Field label="Outcome" htmlFor="ex-outcome">
        <Select id="ex-outcome" value={outcome} onChange={(e) => setOutcome(e.target.value)}>
          <option value="">All outcomes</option>
          {["answered", "abstained", "action_done", "action_pending", "needs_info", "escalated", "handoff", "fallback", "error"].map((o) => <option key={o} value={o}>{label(o)}</option>)}
        </Select></Field></div>
      {list.loading ? <State kind="loading" /> : list.error ? <ErrorState error={list.error} retry={list.reload} /> : !list.data?.length ? <State kind="empty" title="No turns match" /> : (
        <Table caption="Recent assistant turns" head={["When", "Channel", "Intent", "Outcome", "Tools", "Latency", "Tokens", ""]}>
          {list.data.map((e) => (
            <tr key={e.id}>
              <td className="whitespace-nowrap px-3 py-2">{dt(e.created_at)}</td>
              <td className="px-3 py-2">{label(e.channel)}</td>
              <td className="px-3 py-2">{e.intent ? label(e.intent) : "—"}</td>
              <td className="px-3 py-2"><Badge tone={outcomeTone(e.outcome)}>{e.outcome ? label(e.outcome) : "—"}</Badge></td>
              <td className="px-3 py-2">{e.tools.length ? e.tools.map((t) => t.tool).join(", ") : "—"}</td>
              <td className="whitespace-nowrap px-3 py-2">{ms(e.latency_ms)}</td>
              <td className="px-3 py-2">{e.tokens_in + e.tokens_out || "—"}</td>
              <td className="px-3 py-2"><button className="font-medium text-teal-800 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-teal-600" onClick={() => setOpen(e)} aria-label={`View trace for ${dt(e.created_at)}`}>Trace</button></td>
            </tr>))}
        </Table>)}
      {open && (
        <Modal title="Turn trace" onClose={() => setOpen(null)} wide>
          <p className="mb-3 text-xs text-slate-500">Arguments and steps are redacted server-side (contact details and long free text are removed). Correlation id <code>{open.correlation_id}</code>.</p>
          <dl className="mb-3 grid grid-cols-2 gap-2 text-sm sm:grid-cols-4">
            <div><dt className="text-slate-500">Steps</dt><dd>{open.steps}</dd></div>
            <div><dt className="text-slate-500">Total</dt><dd>{ms(open.latency_ms)}</dd></div>
            <div><dt className="text-slate-500">Model</dt><dd>{ms(open.llm_ms)}</dd></div>
            <div><dt className="text-slate-500">Est. cost</dt><dd>{usd(open.estimated_cost_usd)}</dd></div>
          </dl>
          <h3 className="mb-1 text-sm font-semibold text-slate-900">Tool calls</h3>
          {!open.tools.length ? <p className="mb-3 text-sm text-slate-500">No tools were called.</p> : (
            <ul className="mb-3 space-y-2" aria-label="Tool calls">
              {open.tools.map((t, i) => (
                <li key={i} className="rounded-md border border-slate-200 p-2 text-sm">
                  <div className="flex flex-wrap items-center gap-2"><span className="font-medium">{t.tool}</span><Badge tone={t.success ? "green" : "red"}>{t.success ? "ok" : "failed"}</Badge><span className="text-xs text-slate-500">{ms(t.latency_ms)}</span></div>
                  {t.error && <p className="mt-1 text-red-800">{t.error}</p>}
                  <pre className="mt-1 overflow-x-auto rounded bg-slate-50 p-2 text-xs text-slate-700" tabIndex={0} aria-label={`Arguments for ${t.tool}`}>{JSON.stringify(t.arguments, null, 2)}</pre>
                </li>))}
            </ul>)}
          <h3 className="mb-1 text-sm font-semibold text-slate-900">Step trace</h3>
          <pre className="max-h-64 overflow-auto rounded bg-slate-50 p-2 text-xs text-slate-700" tabIndex={0} aria-label="Step trace">{JSON.stringify(open.trace, null, 2)}</pre>
        </Modal>)}
    </div>
  );
}

function Tools({ days }: { days: number }) {
  const { hotel } = useStaff();
  const list = useAsync(() => staff<ToolStat[]>(`hotels/${hotel.id}/ai/tools${qs({ days })}`), [hotel.id, days]);
  if (list.loading) return <State kind="loading" />;
  if (list.error) return <ErrorState error={list.error} retry={list.reload} />;
  if (!list.data?.length) return <State kind="empty" title="No tool calls in this window" />;
  return (
    <Table caption="Tool success rates" head={["Tool", "Calls", "Failures", "Success rate", "Avg latency"]}>
      {list.data.map((t) => (
        <tr key={t.tool}>
          <td className="px-3 py-2 font-medium">{t.tool}</td><td className="px-3 py-2">{t.calls}</td>
          <td className="px-3 py-2">{t.failures}</td>
          <td className="px-3 py-2"><Badge tone={t.success_rate == null || t.success_rate >= 0.95 ? "green" : t.success_rate >= 0.8 ? "amber" : "red"}>{pct(t.success_rate)}</Badge></td>
          <td className="px-3 py-2">{ms(t.avg_latency_ms)}</td>
        </tr>))}
    </Table>
  );
}

function UnansweredList({ days }: { days: number }) {
  const { hotel } = useStaff();
  const list = useAsync(() => staff<Unanswered[]>(`hotels/${hotel.id}/ai/unanswered${qs({ days })}`), [hotel.id, days]);
  if (list.loading) return <State kind="loading" />;
  if (list.error) return <ErrorState error={list.error} retry={list.reload} />;
  if (!list.data?.length) return <State kind="empty" title="Nothing went unanswered" detail="Questions the assistant could not answer appear here so you can add the missing knowledge." />;
  return (
    <Table caption="Questions the assistant could not answer" head={["When", "Question (redacted)", "Intent", "Outcome"]}>
      {list.data.map((u, i) => (
        <tr key={i}><td className="whitespace-nowrap px-3 py-2">{dt(u.at)}</td><td className="px-3 py-2">{u.question ?? "—"}</td><td className="px-3 py-2">{u.intent ? label(u.intent) : "—"}</td><td className="px-3 py-2"><Badge tone={outcomeTone(u.outcome)}>{label(u.outcome)}</Badge></td></tr>))}
    </Table>
  );
}

function LowRated({ days }: { days: number }) {
  const { hotel } = useStaff();
  const list = useAsync(() => staff<Flagged[]>(`hotels/${hotel.id}/ai/flagged${qs({ days: Math.max(days, 30) })}`), [hotel.id, days]);
  if (list.loading) return <State kind="loading" />;
  if (list.error) return <ErrorState error={list.error} retry={list.reload} />;
  if (!list.data?.length) return <State kind="empty" title="No low-rated answers" detail="Answers guests rated 2 or below appear here for review." />;
  return (
    <ul className="space-y-3" aria-label="Low-rated answers">
      {list.data.map((f, i) => (
        <li key={i} className="rounded-lg border border-slate-200 bg-white p-4 text-sm">
          <div className="mb-2 flex flex-wrap items-center gap-2"><Badge tone="red">{f.rating} / 5</Badge>{f.outcome && <Badge tone={outcomeTone(f.outcome)}>{label(f.outcome)}</Badge>}<span className="text-xs text-slate-500">{dt(f.at)}</span></div>
          <p className="text-slate-500">Guest asked</p><p className="mb-2 text-slate-900">{f.question ?? "—"}</p>
          <p className="text-slate-500">Assistant replied</p><p className="mb-2 whitespace-pre-wrap text-slate-900">{f.answer}</p>
          {f.comment && <><p className="text-slate-500">Guest comment</p><p className="text-slate-900">{f.comment}</p></>}
        </li>))}
    </ul>
  );
}

function Audit() {
  const { hotel } = useStaff();
  const [entity, setEntity] = useState("");
  const [action, setAction] = useState("");
  const list = useAsync(() => staff<AuditRow[]>(`hotels/${hotel.id}/audit${qs({ entity_type: entity, action, limit: 100 })}`), [hotel.id, entity, action]);
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-3">
        <Field label="Entity" htmlFor="au-entity"><Select id="au-entity" value={entity} onChange={(e) => setEntity(e.target.value)}><option value="">All</option>{["service_request", "reservation", "knowledge_document", "user", "approval_request", "conversation", "integration"].map((t) => <option key={t} value={t}>{label(t)}</option>)}</Select></Field>
        <Field label="Action starts with" htmlFor="au-action"><Input id="au-action" value={action} onChange={(e) => setAction(e.target.value)} placeholder="e.g. ticket." /></Field>
      </div>
      {list.loading ? <State kind="loading" /> : list.error ? <ErrorState error={list.error} retry={list.reload} /> : !list.data?.length ? <State kind="empty" title="No audit events match" /> : (
        <Table caption="Audit log" head={["When", "Action", "Actor", "Entity", "Details"]}>
          {list.data.map((a) => (
            <tr key={a.id}>
              <td className="whitespace-nowrap px-3 py-2">{dt(a.at)}</td><td className="px-3 py-2 font-medium">{a.action}</td>
              <td className="px-3 py-2">{a.actor_name ?? label(a.actor_type)}</td><td className="px-3 py-2">{a.entity_type ? label(a.entity_type) : "—"}</td>
              <td className="max-w-xs px-3 py-2"><code className="block truncate text-xs text-slate-600" title={JSON.stringify(a.details)}>{Object.keys(a.details ?? {}).length ? JSON.stringify(a.details) : "—"}</code></td>
            </tr>))}
        </Table>)}
    </div>
  );
}
