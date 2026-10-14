"use client";

import { FormEvent, useState } from "react";
import PageHeader from "@/components/staff/PageHeader";
import { useStaff } from "@/components/staff/context";
import { Badge, Button, Card, ErrorState, Field, Input, Notice, State } from "@/components/ui";
import { ApiError, staff } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import { Integration } from "@/lib/types";

const NAMES: Record<string, string> = { twilio_whatsapp: "WhatsApp (Twilio)", telegram: "Telegram" };

export default function IntegrationsPage() {
  const { hotel, can } = useStaff();
  const list = useAsync(() => staff<Integration[]>(`hotels/${hotel.id}/integrations`), [hotel.id]);
  const [msg, setMsg] = useState<{ tone: "success" | "error"; text: string } | null>(null);
  const save = async (i: Integration, enabled: boolean, number?: string) => {
    try {
      await staff(`hotels/${hotel.id}/integrations/${i.provider}`, { method: "PUT", body: { enabled, config: number ? { number } : {} } });
      setMsg({ tone: "success", text: `${NAMES[i.provider]} ${enabled ? "enabled" : "disabled"}.` }); list.reload();
    } catch (e) { setMsg({ tone: "error", text: e instanceof ApiError ? e.message : "Could not save" }); }
  };
  return (
    <>
      <PageHeader title="Channels" subtitle="Where guests can reach the assistant" />
      <Notice tone="info">Secrets (tokens, auth keys) are set in the server environment, never here. “Configured” means settings are present, not that a live message has been verified.</Notice>
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        {list.loading ? <State kind="loading" /> : list.error ? <ErrorState error={list.error} retry={list.reload} /> : list.data?.map((i) => (
          <Card key={i.provider} title={<span className="flex items-center gap-2">{NAMES[i.provider]} <Badge tone={i.enabled && i.credentials_present ? "green" : i.enabled ? "amber" : "slate"}>{i.status}</Badge></span>}>
            <dl className="space-y-1 text-sm">
              <div><dt className="inline text-slate-500">Server credentials: </dt><dd className="inline">{i.credentials_present ? "present" : "missing"}</dd></div>
              <div><dt className="text-slate-500">Webhook URL</dt><dd className="break-all rounded bg-slate-50 p-1.5 font-mono text-xs">{i.webhook_url ?? "Set PUBLIC_BASE_URL to see it"}</dd></div>
              {i.config.number && <div><dt className="inline text-slate-500">Number: </dt><dd className="inline">{i.config.number}</dd></div>}
            </dl>
            {can("admin") && (
              <form className="mt-3 flex flex-wrap items-end gap-2" onSubmit={(e: FormEvent<HTMLFormElement>) => { e.preventDefault(); save(i, true, String(new FormData(e.currentTarget).get("number") ?? "")); }}>
                {i.provider === "twilio_whatsapp" && <Field label="WhatsApp number" htmlFor={`n-${i.provider}`}><Input id={`n-${i.provider}`} name="number" defaultValue={i.config.number ?? ""} placeholder="whatsapp:+14155238886" /></Field>}
                <Button type="submit" size="sm">{i.enabled ? "Update" : "Enable"}</Button>
                {i.enabled && <Button type="button" size="sm" variant="secondary" onClick={() => save(i, false)}>Disable</Button>}
              </form>)}
          </Card>))}
      </div>
      {msg && <div className="mt-4"><Notice tone={msg.tone}>{msg.text}</Notice></div>}
    </>
  );
}
