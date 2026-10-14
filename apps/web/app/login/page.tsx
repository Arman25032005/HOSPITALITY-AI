"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { FormEvent, Suspense, useState } from "react";
import { Button, Field, Input, Notice } from "@/components/ui";

function LoginForm() {
  const params = useSearchParams();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    setBusy(true);
    setError(null);
    try {
      const r = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "content-type": "application/json", "x-requested-with": "hospitality" },
        body: JSON.stringify({ email: f.get("email"), password: f.get("password") }),
      });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? "Sign-in failed");
      const next = params.get("next");
      window.location.href = next && next.startsWith("/staff") ? next : "/staff";
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed");
      setBusy(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="space-y-4" noValidate>
      <Field label="Work email" htmlFor="email">
        <Input id="email" name="email" type="email" required autoComplete="username" autoFocus />
      </Field>
      <Field label="Password" htmlFor="password">
        <Input id="password" name="password" type="password" required autoComplete="current-password" />
      </Field>
      {error && <Notice tone="error">{error}</Notice>}
      <Button type="submit" disabled={busy} className="w-full">{busy ? "Signing in…" : "Sign in"}</Button>
    </form>
  );
}

export default function LoginPage() {
  return (
    <main className="grid min-h-screen place-items-center bg-gradient-to-b from-teal-50 to-slate-50 px-4">
      <div className="w-full max-w-sm rounded-xl border border-slate-200 bg-white p-7 shadow-sm">
        <p className="text-sm font-semibold text-teal-800">HospitalityOS</p>
        <h1 className="mt-1 text-2xl font-semibold text-slate-900">Staff sign in</h1>
        <p className="mb-6 mt-1 text-sm text-slate-500">Operations dashboard for hotel teams.</p>
        <Suspense><LoginForm /></Suspense>
        <p className="mt-6 text-center text-sm text-slate-500">
          Looking for the guest concierge? <Link href="/" className="font-medium text-teal-800 underline">Choose a hotel</Link>
        </p>
      </div>
    </main>
  );
}
