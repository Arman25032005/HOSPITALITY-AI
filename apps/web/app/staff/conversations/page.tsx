"use client";

import { useSearchParams } from "next/navigation";
import { FormEvent, Suspense, useEffect, useRef, useState } from "react";
import PageHeader from "@/components/staff/PageHeader";
import { useStaff } from "@/components/staff/context";
import { Badge, Button, ErrorState, Notice, Select, State, Textarea, statusTone } from "@/components/ui";
import { ApiError, qs, staff } from "@/lib/api";
import { dt, label } from "@/lib/format";
import { useAsync } from "@/lib/hooks";
import { ChatMessage, ConversationRow } from "@/lib/types";

function Thread({ id, hotelId, status, onChanged }: { id: string; hotelId: string; status: string; onChanged: () => void }) {
  const base = `hotels/${hotelId}/conversations/${id}`;
  const msgs = useAsync(() => staff<ChatMessage[]>(`${base}/messages`), [id]);
  const [text, setText] = useState("");
  const [msg, setMsg] = useState<{ tone: "success" | "error"; text: string } | null>(null);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [msgs.data]);
  useEffect(() => { const t = setInterval(msgs.reload, 15000); return () => clearInterval(t); }, [msgs.reload]);
  const send = async (e: FormEvent) => {
    e.preventDefault();
    try { await staff(`${base}/staff-messages`, { method: "POST", body: { text } }); setText(""); msgs.reload(); }
    catch (err) { setMsg({ tone: "error", text: err instanceof ApiError ? err.message : "Could not send" }); }
  };
  const release = async () => {
    try { await staff(`${base}/release`, { method: "POST" }); setMsg({ tone: "success", text: "Conversation handed back to the assistant." }); onChanged(); }
    catch (err) { setMsg({ tone: "error", text: err instanceof ApiError ? err.message : "Could not release" }); }
  };
  return (
    <div className="flex h-[70vh] flex-col rounded-lg border border-slate-200 bg-white">
      <div className="flex items-center justify-between border-b border-slate-100 px-4 py-2">
        <Badge tone={statusTone(status)}>{label(status)}</Badge>
        {status === "handed_off" && <Button size="sm" variant="secondary" onClick={release}>Hand back to assistant</Button>}
      </div>
      <div className="flex-1 space-y-2 overflow-y-auto p-4" role="log" aria-label="Messages">
        {msgs.loading && !msgs.data ? <State kind="loading" /> : msgs.error ? <ErrorState error={msgs.error} retry={msgs.reload} /> : msgs.data?.map((m) => (
          <div key={m.id} className={`flex ${m.role === "user" ? "justify-start" : "justify-end"}`}>
            <div className={`max-w-[80%] rounded-2xl px-3 py-2 text-sm ${m.role === "user" ? "bg-slate-100" : m.role === "staff" ? "bg-amber-100 text-amber-950" : "bg-teal-50 text-teal-950"}`}>
              <p className="text-xs font-semibold text-slate-500">{m.role === "user" ? "Guest" : m.role === "staff" ? "Staff" : "Assistant"} · {dt(m.created_at)}</p>
              <p className="whitespace-pre-wrap break-words">{m.content}</p>
            </div>
          </div>))}
        <div ref={end} />
      </div>
      {msg && <div className="px-4 pb-2"><Notice tone={msg.tone}>{msg.text}</Notice></div>}
      <form onSubmit={send} className="flex gap-2 border-t border-slate-100 p-3">
        <label htmlFor="reply" className="sr-only">Reply to guest</label>
        <Textarea id="reply" rows={2} value={text} onChange={(e) => setText(e.target.value)} placeholder="Reply to the guest (sent on their channel)" maxLength={2000} />
        <Button type="submit" disabled={!text.trim()}>Send</Button>
      </form>
    </div>
  );
}

function Conversations() {
  const { hotel } = useStaff();
  const params = useSearchParams();
  const [status, setStatus] = useState("");
  const [sel, setSel] = useState<string | null>(params.get("open"));
  const list = useAsync(() => staff<ConversationRow[]>(`hotels/${hotel.id}/conversations${qs({ status })}`), [hotel.id, status]);
  const current = list.data?.find((c) => c.id === sel);
  return (
    <>
      <PageHeader title="Conversations" subtitle="Guest chats across web, WhatsApp and Telegram"
        actions={<Select aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)} className="w-44"><option value="">All</option><option value="handed_off">Needs a person</option><option value="active">With the assistant</option></Select>} />
      <div className="grid gap-4 lg:grid-cols-[20rem_1fr]">
        <div>
          {list.loading ? <State kind="loading" /> : list.error ? <ErrorState error={list.error} retry={list.reload} /> : !list.data?.length ? <State kind="empty" title="No conversations" /> : (
            <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200 bg-white" aria-label="Conversations">
              {list.data.map((c) => (
                <li key={c.id}>
                  <button onClick={() => setSel(c.id)} aria-current={sel === c.id} className={`w-full px-3 py-2.5 text-left text-sm hover:bg-slate-50 ${sel === c.id ? "bg-teal-50" : ""}`}>
                    <span className="flex items-center justify-between gap-2"><span className="font-medium">{c.guest_name ?? "Guest"}</span><Badge tone={statusTone(c.status)}>{label(c.status)}</Badge></span>
                    <span className="text-xs text-slate-500">{label(c.channel)} · {dt(c.last_activity)}{c.handoff_ticket ? ` · ${c.handoff_ticket}` : ""}</span>
                  </button>
                </li>))}
            </ul>)}
        </div>
        {sel ? <Thread key={sel} id={sel} hotelId={hotel.id} status={current?.status ?? "active"} onChanged={list.reload} /> : <State kind="empty" title="Select a conversation" detail="Choose one from the list to read it and reply." />}
      </div>
    </>
  );
}
export default function Page() { return <Suspense><Conversations /></Suspense>; }
