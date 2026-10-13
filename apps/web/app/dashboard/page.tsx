"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { fetchMe, Me } from "@/lib/api";

export default function Dashboard() {
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);

  useEffect(() => {
    const token = sessionStorage.getItem("token");
    if (!token) return void router.replace("/login");
    fetchMe(token).then(setMe).catch(() => {
      sessionStorage.removeItem("token");
      router.replace("/login");
    });
  }, [router]);

  if (!me) return <p className="p-6 text-slate-600">Loading…</p>;
  return (
    <main className="mx-auto max-w-3xl p-6">
      <h1 className="text-2xl font-semibold">Operations dashboard</h1>
      <p className="mt-2 text-slate-600">
        Signed in as {me.full_name} ({me.role}). Tickets, reservations, and analytics views are
        not yet implemented in this build.
      </p>
    </main>
  );
}
