"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { Button, Field, Input, Notice, Spinner } from "@/components/ui";
import { ApiError, guest } from "@/lib/api";
import { ChatMessage, Citation } from "@/lib/types";

interface Session { token: string; conversation_id: string; hotel_name: string }
interface Reply {
  reply: string; outcome: string; awaiting_confirmation: boolean; citations: Citation[];
  ticket_numbers: string[]; reservation_codes: string[]; message_id: string;
}
interface Row extends ChatMessage { pending?: boolean; failed?: boolean; confirm?: boolean }

const newId = () => (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`);
const SUGGESTIONS = ["What time is breakfast?", "I'd like to book a room", "Can I get a late checkout?", "My AC is not working"];

export default function ChatPage() {
  const { org, hotel } = useParams<{ org: string; hotel: string }>();
  const key = `hos_guest_${org}_${hotel}`;
  const [session, setSession] = useState<Session | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showVerify, setShowVerify] = useState(false);
  const [verifyMsg, setVerifyMsg] = useState<{ tone: "success" | "error"; text: string } | null>(null);
  const [rated, setRated] = useState<Record<string, number>>({});
  const bottom = useRef<HTMLDivElement>(null);

  const start = useCallback(async () => {
    setError(null);
    try {
      const saved = sessionStorage.getItem(key);
      if (saved) {
        const s: Session = JSON.parse(saved);
        try {
          setRows(await guest<ChatMessage[]>("chat/messages", s.token));
          setSession(s);
          return;
        } catch (e) {
          if (!(e instanceof ApiError) || ![401, 404].includes(e.status)) throw e;
          sessionStorage.removeItem(key);
        }
      }
      const s = await guest<Session>("chat/sessions", null, { method: "POST", body: { org_slug: org, hotel_slug: hotel } });
      sessionStorage.setItem(key, JSON.stringify(s));
      setSession(s);
      setRows([]);
    } catch (e) {
      setError(e instanceof ApiError && e.status === 404 ? "We couldn't find that hotel." : e instanceof ApiError && e.status === 429
        ? "Too many people are connecting right now. Please try again in a minute." : "We couldn't start the chat. Please try again.");
    }
  }, [key, org, hotel]);
  useEffect(() => { start(); }, [start]);
  useEffect(() => { bottom.current?.scrollIntoView({ block: "end" }); }, [rows, busy]);
  // Pick up replies from hotel staff (e.g. after a handoff) without a page refresh.
  useEffect(() => {
    if (!session) return;
    const t = setInterval(async () => {
      if (busy || document.hidden) return;
      try {
        const h = await guest<ChatMessage[]>("chat/messages", session.token);
        setRows((prev) => (h.length > prev.filter((r) => !r.pending && !r.failed).length ? h : prev));
      } catch { /* transient; the next tick retries */ }
    }, 6000);
    return () => clearInterval(t);
  }, [session, busy]);

  async function send(content: string, id = newId()) {
    if (!session || !content.trim() || busy) return;
    setBusy(true);
    setError(null);
    setText("");
    setRows((r) => [...r.filter((x) => !x.confirm), { id, role: "user", content, created_at: new Date().toISOString(), pending: true }]);
    try {
      const out = await guest<Reply>("chat/messages", session.token, { method: "POST", body: { text: content, client_message_id: id } });
      setRows((r) => [
        ...r.map((x) => (x.id === id ? { ...x, pending: false } : x)),
        { id: out.message_id, role: "assistant", content: out.reply, created_at: new Date().toISOString(), citations: out.citations, confirm: out.awaiting_confirmation },
      ]);
    } catch (e) {
      setRows((r) => r.map((x) => (x.id === id ? { ...x, pending: false, failed: true } : x)));
      setError(e instanceof ApiError && e.status === 429 ? "You're sending messages too quickly. Please wait a moment."
        : e instanceof ApiError ? e.message : "Message not sent. Check your connection and retry.");
    } finally {
      setBusy(false);
    }
  }

  async function verify(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!session) return;
    const f = new FormData(e.currentTarget);
    try {
      const r = await guest<{ verified: boolean; attempts_left: number }>("chat/verify", session.token, {
        method: "POST", body: { confirmation_code: String(f.get("code")).trim().toUpperCase(), last_name: String(f.get("last")).trim() } });
      setVerifyMsg(r.verified ? { tone: "success", text: "Your stay is verified. You can now ask for help with your room." }
        : { tone: "error", text: `We couldn't match those details (${r.attempts_left} attempts left).` });
      if (r.verified) setShowVerify(false);
    } catch (err) {
      setVerifyMsg({ tone: "error", text: err instanceof ApiError && err.status === 403 ? "Too many attempts. Please contact the front desk." : "Verification failed. Please try again." });
    }
  }

  async function rate(m: Row, rating: number) {
    if (!session) return;
    setRated((r) => ({ ...r, [m.id]: rating }));
    await guest(`chat/messages/${m.id}/feedback`, session.token, { method: "POST", body: { rating } }).catch(() => setRated((r) => { const n = { ...r }; delete n[m.id]; return n; }));
  }

  return (
    <div className="mx-auto flex h-[100dvh] max-w-2xl flex-col bg-white shadow-sm sm:my-0">
      <header className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
        <div>
          <p className="text-xs font-medium text-teal-800">Concierge</p>
          <h1 className="text-base font-semibold text-slate-900">{session?.hotel_name ?? "Connecting…"}</h1>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="secondary" size="sm" onClick={() => setShowVerify(!showVerify)} aria-expanded={showVerify}>Verify my stay</Button>
          <Link href="/" className="text-sm text-slate-500 underline">Hotels</Link>
        </div>
      </header>

      {showVerify && (
        <form onSubmit={verify} className="space-y-3 border-b border-slate-200 bg-slate-50 p-4">
          <p className="text-sm text-slate-600">Enter your confirmation code and last name to unlock requests for your room.</p>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Confirmation code" htmlFor="code"><Input id="code" name="code" required placeholder="R-XXXXXXXX" autoComplete="off" /></Field>
            <Field label="Last name" htmlFor="last"><Input id="last" name="last" required autoComplete="family-name" /></Field>
          </div>
          <Button type="submit" size="sm">Verify</Button>
        </form>
      )}
      {verifyMsg && <div className="px-4 pt-3"><Notice tone={verifyMsg.tone}>{verifyMsg.text}</Notice></div>}

      <div className="flex-1 overflow-y-auto px-4 py-4" role="log" aria-live="polite" aria-label="Conversation">
        {!session && !error && <Spinner label="Connecting to the concierge" />}
        {session && rows.length === 0 && (
          <div className="py-8 text-center">
            <p className="text-sm text-slate-600">Hello! Ask me about the hotel, book a room, or get help during your stay.</p>
            <div className="mt-4 flex flex-wrap justify-center gap-2">
              {SUGGESTIONS.map((s) => <Button key={s} variant="secondary" size="sm" onClick={() => send(s)}>{s}</Button>)}
            </div>
          </div>
        )}
        <ul className="space-y-3">
          {rows.map((m) => {
            const mine = m.role === "user";
            return (
              <li key={m.id} className={`flex ${mine ? "justify-end" : "justify-start"}`}>
                <div className={`max-w-[85%] rounded-2xl px-3.5 py-2 text-sm ${mine ? "bg-teal-700 text-white" : m.role === "staff" ? "bg-amber-50 text-amber-950 ring-1 ring-amber-200" : "bg-slate-100 text-slate-900"} ${m.pending ? "opacity-60" : ""}`}>
                  {m.role === "staff" && <p className="mb-0.5 text-xs font-semibold">Hotel team</p>}
                  <p className="whitespace-pre-wrap break-words">{m.content}</p>
                  {m.failed && <button className="mt-1 text-xs underline" onClick={() => send(m.content, m.id)}>Not sent. Tap to retry</button>}
                  {!!m.citations?.length && (
                    <details className="mt-2 text-xs text-slate-600">
                      <summary className="cursor-pointer">Sources</summary>
                      <ul className="mt-1 list-disc pl-4">{m.citations.map((c) => <li key={c.chunk_id}>{c.heading || c.title}</li>)}</ul>
                    </details>
                  )}
                  {m.role === "assistant" && (
                    <div className="mt-2 flex items-center gap-2 text-xs text-slate-500">
                      {rated[m.id] ? <span>Thanks for your feedback</span> : <>
                        <span>Was this helpful?</span>
                        <button className="rounded px-1.5 py-0.5 underline hover:bg-white" onClick={() => rate(m, 5)}>Yes</button>
                        <button className="rounded px-1.5 py-0.5 underline hover:bg-white" onClick={() => rate(m, 1)}>No</button>
                      </>}
                    </div>
                  )}
                  {m.confirm && !busy && (
                    <div className="mt-2 flex gap-2">
                      <Button size="sm" onClick={() => send("Yes")}>Yes, confirm</Button>
                      <Button size="sm" variant="secondary" onClick={() => send("No")}>No</Button>
                    </div>
                  )}
                </div>
              </li>
            );
          })}
          {busy && <li className="text-sm text-slate-500" role="status">Concierge is typing…</li>}
        </ul>
        <div ref={bottom} />
      </div>

      {error && <div className="px-4 pb-2"><Notice tone="error">{error}{!session && <> <button className="underline" onClick={start}>Retry</button></>}</Notice></div>}
      <form onSubmit={(e) => { e.preventDefault(); send(text); }} className="flex items-end gap-2 border-t border-slate-200 p-3">
        <label htmlFor="msg" className="sr-only">Message</label>
        <textarea id="msg" value={text} onChange={(e) => setText(e.target.value)} rows={1} maxLength={1000} disabled={!session}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(text); } }}
          placeholder="Type your message" className="max-h-32 min-h-[2.5rem] flex-1 resize-none rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-teal-600 focus:outline-none focus:ring-1 focus:ring-teal-600" />
        <Button type="submit" disabled={!session || busy || !text.trim()}>Send</Button>
      </form>
      <p className="px-4 pb-3 text-center text-xs text-slate-600">Automated assistant. Staff confirm anything that affects your booking or stay. Never share card numbers here.</p>
    </div>
  );
}
