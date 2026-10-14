"use client";

import { FormEvent, useMemo, useState } from "react";
import PageHeader from "@/components/staff/PageHeader";
import { useStaff } from "@/components/staff/context";
import { Badge, Button, Card, ErrorState, Field, Input, Modal, Notice, Select, State, Table } from "@/components/ui";
import { ApiError, staff, staffUpload } from "@/lib/api";
import { day, dt, label } from "@/lib/format";
import { useAsync } from "@/lib/hooks";
import { AskResult, KnowledgeDoc, KnowledgeDocDetail, Passage } from "@/lib/types";

const DOC_TYPES = ["policy", "amenity", "room", "dining", "spa", "local", "faq", "internal", "other"];
const ACCEPT = ".pdf,.docx,.txt,.md,.csv,.html,.htm";

const statusTone = (d: KnowledgeDoc) =>
  d.status === "failed" ? "red" : !d.is_active ? "slate" : d.status === "indexed" ? "green" : "amber";

export default function KnowledgePage() {
  const { hotel } = useStaff();
  const base = `hotels/${hotel.id}/knowledge`;
  const docs = useAsync(() => staff<KnowledgeDoc[]>(`${base}/documents`), [hotel.id]);
  const [msg, setMsg] = useState<{ tone: "success" | "error"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [detail, setDetail] = useState<KnowledgeDocDetail | null>(null);
  const [confirm, setConfirm] = useState<{ doc: KnowledgeDoc; action: "deactivate" | "delete" } | null>(null);

  // Group every version of a document under its key; newest first.
  const groups = useMemo(() => {
    const m = new Map<string, KnowledgeDoc[]>();
    for (const d of docs.data ?? []) m.set(d.doc_key, [...(m.get(d.doc_key) ?? []), d]);
    return [...m.values()].map((v) => v.sort((a, b) => b.version - a.version));
  }, [docs.data]);

  const fail = (e: unknown, fallback: string) => setMsg({ tone: "error", text: e instanceof ApiError ? e.message : fallback });

  const upload = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = e.currentTarget;
    const f = new FormData(form);
    const file = f.get("file");
    if (!(file instanceof File) || !file.size) { setMsg({ tone: "error", text: "Choose a file to upload." }); return; }
    if (!f.get("doc_key")) f.delete("doc_key");
    if (!f.get("effective_date")) f.delete("effective_date");
    f.set("is_confidential", f.get("is_confidential") ? "true" : "false");
    setBusy(true); setMsg(null);
    try {
      const d = await staffUpload<KnowledgeDoc>(`${base}/documents`, f);
      setMsg(d.status === "failed"
        ? { tone: "error", text: `“${d.title}” was saved but ingestion failed. Open it to see the error.` }
        : { tone: "success", text: `“${d.title}” ingested as version ${d.version}.` });
      form.reset(); docs.reload();
    } catch (x) { fail(x, "Upload failed"); } finally { setBusy(false); }
  };

  const open = async (d: KnowledgeDoc) => {
    try { setDetail(await staff<KnowledgeDocDetail>(`${base}/documents/${d.id}`)); } catch (x) { fail(x, "Could not load the document"); }
  };
  const reindex = async (d: KnowledgeDoc) => {
    setBusy(true);
    try { await staff(`${base}/documents/${d.id}/reindex`, { method: "POST" }); setMsg({ tone: "success", text: `“${d.title}” re-indexed.` }); docs.reload(); setDetail(null); }
    catch (x) { fail(x, "Re-index failed"); } finally { setBusy(false); }
  };
  const runConfirmed = async () => {
    if (!confirm) return;
    const { doc, action } = confirm;
    setBusy(true);
    try {
      if (action === "delete") await staff(`${base}/documents/${doc.id}`, { method: "DELETE" });
      else await staff(`${base}/documents/${doc.id}/deactivate`, { method: "POST" });
      setMsg({ tone: "success", text: `“${doc.title}” ${action === "delete" ? "deleted" : "deactivated"}.` });
      setConfirm(null); docs.reload();
    } catch (x) { setConfirm(null); fail(x, "That action failed"); } finally { setBusy(false); }
  };

  return (
    <>
      <PageHeader title="Knowledge" subtitle="The only source the assistant may answer from" />
      <div className="space-y-4">
        {msg && <Notice tone={msg.tone}>{msg.text}</Notice>}
        <Card title="Upload a document">
          <form onSubmit={upload} className="grid gap-3 md:grid-cols-3" aria-label="Upload document">
            <Field label="File" htmlFor="kb-file" hint="PDF, DOCX, TXT, MD, CSV or HTML"><Input id="kb-file" name="file" type="file" accept={ACCEPT} required /></Field>
            <Field label="Title" htmlFor="kb-title"><Input id="kb-title" name="title" required maxLength={300} /></Field>
            <Field label="Type" htmlFor="kb-type"><Select id="kb-type" name="doc_type" defaultValue="policy">{DOC_TYPES.map((t) => <option key={t} value={t}>{label(t)}</option>)}</Select></Field>
            <Field label="Language" htmlFor="kb-lang"><Input id="kb-lang" name="language" defaultValue="en" maxLength={10} /></Field>
            <Field label="Document key" htmlFor="kb-key" hint="Re-uploading the same key creates a new version"><Input id="kb-key" name="doc_key" maxLength={200} placeholder="optional" /></Field>
            <Field label="Effective date" htmlFor="kb-eff"><Input id="kb-eff" name="effective_date" type="date" /></Field>
            <label className="flex items-center gap-2 text-sm text-slate-700 md:col-span-2">
              <input type="checkbox" name="is_confidential" className="h-4 w-4 rounded border-slate-300" />
              Confidential: staff-only, never shown to guests
            </label>
            <div className="md:justify-self-end"><Button type="submit" disabled={busy}>{busy ? "Working…" : "Upload and index"}</Button></div>
          </form>
        </Card>

        <Card title="Documents">
          {docs.loading ? <State kind="loading" /> : docs.error ? <ErrorState error={docs.error} retry={docs.reload} /> : !groups.length ? <State kind="empty" title="No documents yet" detail="Upload a policy, menu or FAQ so the assistant has something to answer from." /> : (
            <Table caption="Knowledge documents and their versions" head={["Title", "Type", "Version", "Status", "Updated", "Actions"]}>
              {groups.flatMap((versions) => versions.map((d, i) => (
                <tr key={d.id} className={i > 0 ? "bg-slate-50/60" : ""}>
                  <td className="px-3 py-2">
                    <button className="text-left font-medium text-teal-800 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-teal-600" onClick={() => open(d)} aria-label={`Open ${d.title} version ${d.version}`}>{d.title}</button>
                    <div className="mt-0.5 flex flex-wrap gap-1">
                      {d.is_confidential && <Badge tone="purple">Confidential</Badge>}
                      {d.is_demo && <Badge tone="amber">Demo content</Badge>}
                      {i > 0 && <span className="text-xs text-slate-500">older version</span>}
                    </div>
                  </td>
                  <td className="px-3 py-2">{label(d.doc_type)}</td>
                  <td className="px-3 py-2">v{d.version}</td>
                  <td className="px-3 py-2"><Badge tone={statusTone(d)}>{d.status === "failed" ? "Failed" : d.is_active ? label(d.status) : "Inactive"}</Badge></td>
                  <td className="px-3 py-2 whitespace-nowrap">{day(d.effective_date) !== "—" ? day(d.effective_date) : dt(d.created_at)}</td>
                  <td className="px-3 py-2"><div className="flex flex-wrap gap-1.5">
                    <Button size="sm" variant="secondary" onClick={() => reindex(d)} disabled={busy} aria-label={`Re-index ${d.title} version ${d.version}`}>Re-index</Button>
                    {d.is_active && <Button size="sm" variant="secondary" onClick={() => setConfirm({ doc: d, action: "deactivate" })} disabled={busy} aria-label={`Deactivate ${d.title} version ${d.version}`}>Deactivate</Button>}
                    <Button size="sm" variant="danger" onClick={() => setConfirm({ doc: d, action: "delete" })} disabled={busy} aria-label={`Delete ${d.title} version ${d.version}`}>Delete</Button>
                  </div></td>
                </tr>
              )))}
            </Table>
          )}
        </Card>

        <Tester base={base} />
      </div>

      {detail && (
        <Modal title={`${detail.title} · v${detail.version}`} onClose={() => setDetail(null)} wide>
          <dl className="grid gap-2 text-sm sm:grid-cols-2">
            <div><dt className="text-slate-500">Status</dt><dd>{label(detail.status)}{detail.is_active ? "" : " (inactive)"}</dd></div>
            <div><dt className="text-slate-500">Passages indexed</dt><dd>{detail.chunk_count}</dd></div>
            <div><dt className="text-slate-500">Source file</dt><dd className="break-all">{detail.source ?? "—"}</dd></div>
            <div><dt className="text-slate-500">Language</dt><dd>{detail.language}</dd></div>
          </dl>
          <h3 className="mb-2 mt-4 text-sm font-semibold text-slate-900">Ingestion history</h3>
          {!detail.jobs.length ? <p className="text-sm text-slate-500">No ingestion jobs recorded.</p> : (
            <ul className="divide-y divide-slate-100 rounded-md border border-slate-200 text-sm" aria-label="Ingestion jobs">
              {detail.jobs.map((j) => (
                <li key={j.id} className="p-3">
                  <div className="flex flex-wrap items-center gap-2"><Badge tone={j.status === "failed" ? "red" : j.status === "succeeded" ? "green" : "amber"}>{label(j.status)}</Badge><span className="text-slate-500">{dt(j.created_at)}</span>{j.duration_ms != null && <span className="text-slate-500">{j.duration_ms} ms</span>}</div>
                  {j.error && <p className="mt-1 break-words text-red-800">{j.error}</p>}
                </li>))}
            </ul>)}
          <div className="mt-4 flex justify-end gap-2"><Button variant="secondary" onClick={() => setDetail(null)}>Close</Button><Button onClick={() => reindex(detail)} disabled={busy}>Re-index</Button></div>
        </Modal>)}

      {confirm && (
        <Modal title={confirm.action === "delete" ? "Delete document?" : "Deactivate document?"} onClose={() => setConfirm(null)}>
          <p className="text-sm text-slate-700">
            {confirm.action === "delete"
              ? <>“{confirm.doc.title}” (v{confirm.doc.version}) and its indexed passages will be permanently removed. This cannot be undone.</>
              : <>The assistant will stop using “{confirm.doc.title}” (v{confirm.doc.version}) immediately. You can re-index it later.</>}
          </p>
          <div className="mt-4 flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setConfirm(null)}>Cancel</Button>
            <Button variant={confirm.action === "delete" ? "danger" : "primary"} onClick={runConfirmed} disabled={busy}>{confirm.action === "delete" ? "Delete" : "Deactivate"}</Button>
          </div>
        </Modal>)}
    </>
  );
}

/** Try a question exactly as a guest (or staff) would be answered, before relying on a document. */
function Tester({ base }: { base: string }) {
  const [q, setQ] = useState("");
  const [audience, setAudience] = useState<"guest" | "staff">("guest");
  const [res, setRes] = useState<{ passages: Passage[]; ask: AskResult } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const run = async (e: FormEvent) => {
    e.preventDefault();
    if (!q.trim()) return;
    setBusy(true); setErr(null);
    try {
      const [passages, ask] = await Promise.all([
        staff<Passage[]>(`${base}/search`, { method: "POST", body: { query: q, audience, top_k: 5 } }),
        staff<AskResult>(`${base}/ask`, { method: "POST", body: { question: q, audience } }),
      ]);
      setRes({ passages, ask });
    } catch (x) { setErr(x instanceof ApiError ? x.message : "Search failed"); setRes(null); } finally { setBusy(false); }
  };
  return (
    <Card title="Test retrieval">
      <form onSubmit={run} className="flex flex-wrap items-end gap-3" aria-label="Test retrieval">
        <div className="min-w-[14rem] flex-1"><Field label="Question" htmlFor="kb-q"><Input id="kb-q" value={q} onChange={(e) => setQ(e.target.value)} maxLength={500} placeholder="What time is check-out?" /></Field></div>
        <Field label="Ask as" htmlFor="kb-aud"><Select id="kb-aud" value={audience} onChange={(e) => setAudience(e.target.value as "guest" | "staff")}><option value="guest">Guest</option><option value="staff">Staff</option></Select></Field>
        <Button type="submit" disabled={busy || !q.trim()}>{busy ? "Searching…" : "Search"}</Button>
      </form>
      {err && <div className="mt-3"><Notice tone="error">{err}</Notice></div>}
      {res && (
        <div className="mt-4 space-y-3" aria-live="polite">
          <div className="rounded-md border border-slate-200 bg-slate-50 p-3 text-sm">
            <p className="mb-1 flex items-center gap-2 font-semibold text-slate-900">Assistant answer <Badge tone={res.ask.abstained ? "amber" : "green"}>{res.ask.abstained ? "Abstained" : "Answered"}</Badge></p>
            <p className="whitespace-pre-wrap text-slate-800">{res.ask.answer}</p>
            {res.ask.abstained && <p className="mt-1 text-xs text-slate-500">Reason: {res.ask.reason}</p>}
            {res.ask.citations.length > 0 && <p className="mt-2 text-xs text-slate-500">Sources: {res.ask.citations.map((c) => `${c.title} v${c.version}${c.heading ? ` › ${c.heading}` : ""}`).join("; ")}</p>}
          </div>
          <h3 className="text-sm font-semibold text-slate-900">Top passages</h3>
          {!res.passages.length ? <p className="text-sm text-slate-500">No passages cleared the relevance thresholds.</p> : (
            <ol className="space-y-2">
              {res.passages.map((p) => (
                <li key={p.chunk_id} className="rounded-md border border-slate-200 p-3 text-sm">
                  <p className="font-medium text-slate-900">{p.title} v{p.version}{p.heading ? ` › ${p.heading}` : ""}</p>
                  <p className="text-xs text-slate-500">similarity {p.cosine.toFixed(2)} · term coverage {Math.round(p.coverage * 100)}%</p>
                  <p className="mt-1 whitespace-pre-wrap text-slate-700">{p.text}</p>
                </li>))}
            </ol>)}
        </div>)}
    </Card>
  );
}
