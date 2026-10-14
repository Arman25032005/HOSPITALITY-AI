"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { Badge, Button, Spinner, State } from "@/components/ui";
import { ALL_STAFF, FRONT_OFFICE, MANAGERS, StaffContext } from "@/components/staff/context";
import { staff } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import { dt, label } from "@/lib/format";
import { Hotel, Me, Notification, Role } from "@/lib/types";

const NAV: { href: string; text: string; roles: Role[] }[] = [
  { href: "/staff", text: "Overview", roles: ALL_STAFF },
  { href: "/staff/tickets", text: "Tickets", roles: ALL_STAFF },
  { href: "/staff/reservations", text: "Reservations", roles: FRONT_OFFICE },
  { href: "/staff/conversations", text: "Conversations", roles: FRONT_OFFICE },
  { href: "/staff/approvals", text: "Approvals", roles: FRONT_OFFICE },
  { href: "/staff/integrations", text: "Channels", roles: MANAGERS },
];

function Bell({ hotelId }: { hotelId: string }) {
  const [open, setOpen] = useState(false);
  const { data, reload } = useAsync(() => staff<Notification[]>(`hotels/${hotelId}/notifications`), [hotelId]);
  useEffect(() => { const t = setInterval(reload, 20000); return () => clearInterval(t); }, [reload]);
  const unread = (data ?? []).filter((n) => !n.read).length;
  const read = async (n: Notification) => { await staff(`hotels/${hotelId}/notifications/${n.id}/read`, { method: "POST" }).catch(() => {}); reload(); };
  return (
    <div className="relative">
      <Button variant="ghost" size="sm" aria-haspopup="true" aria-expanded={open} onClick={() => setOpen(!open)} aria-label={`Notifications, ${unread} unread`}>
        Alerts{unread > 0 && <span className="ml-1.5 rounded-full bg-red-600 px-1.5 text-xs font-semibold text-white">{unread}</span>}
      </Button>
      {open && (
        <div className="absolute right-0 z-30 mt-1 w-80 max-w-[90vw] rounded-lg border border-slate-200 bg-white shadow-lg">
          <ul className="max-h-96 divide-y divide-slate-100 overflow-y-auto" aria-label="Notifications">
            {(data ?? []).length === 0 && <li className="p-4 text-sm text-slate-500">No notifications.</li>}
            {(data ?? []).map((n) => (
              <li key={n.id} className={`flex items-start gap-2 p-3 text-sm ${n.read ? "opacity-60" : ""}`}>
                <Badge tone={n.severity === "critical" ? "red" : n.severity === "warning" ? "amber" : "blue"}>{n.severity}</Badge>
                <div className="min-w-0 flex-1">
                  <p className="font-medium text-slate-900">{n.title}</p>
                  <p className="text-xs text-slate-500">{dt(n.created_at)}</p>
                </div>
                {!n.read && <Button size="sm" variant="ghost" onClick={() => read(n)}>Mark read</Button>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export default function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const router = useRouter();
  const [hotelId, setHotelId] = useState<string | null>(null);
  const me = useAsync(() => staff<Me>("auth/me"), []);
  const hotels = useAsync(() => staff<Hotel[]>("hotels"), []);
  const [navOpen, setNavOpen] = useState(false);

  useEffect(() => {
    if (!hotels.data?.length) return;
    const saved = typeof window !== "undefined" ? localStorage.getItem("hos_hotel") : null;
    setHotelId(hotels.data.find((h) => h.id === saved)?.id ?? hotels.data[0].id);
  }, [hotels.data]);
  useEffect(() => setNavOpen(false), [path]);

  const hotel = hotels.data?.find((h) => h.id === hotelId) ?? null;
  const ctx = useMemo(() => me.data && hotels.data && hotel ? {
    me: me.data, hotels: hotels.data, hotel,
    setHotelId: (id: string) => { localStorage.setItem("hos_hotel", id); setHotelId(id); },
    can: (...roles: Role[]) => roles.includes(me.data!.role),
  } : null, [me.data, hotels.data, hotel]);

  const signOut = async () => {
    await fetch("/api/auth/logout", { method: "POST", headers: { "x-requested-with": "hospitality" } });
    router.push("/login");
  };

  if (me.loading || hotels.loading) return <div className="p-10"><Spinner label="Loading your workspace" /></div>;
  if (me.error || hotels.error) return <div className="p-6"><State kind="error" detail="We couldn't load your session." action={<Button onClick={() => router.push("/login")}>Sign in again</Button>} /></div>;
  if (!ctx) return <div className="p-6"><State kind="denied" title="No hotel assigned" detail="Ask an administrator to assign you to a hotel." /></div>;

  const items = NAV.filter((n) => n.roles.includes(ctx.me.role));
  return (
    <StaffContext.Provider value={ctx}>
      <div className="min-h-screen bg-slate-50 md:flex">
        <aside className={`${navOpen ? "block" : "hidden"} border-b border-slate-200 bg-white md:block md:min-h-screen md:w-56 md:border-b-0 md:border-r`}>
          <div className="px-4 py-4"><p className="text-sm font-semibold text-teal-800">HospitalityOS</p><p className="text-xs text-slate-500">Operations</p></div>
          <nav aria-label="Main" className="px-2 pb-4">
            <ul className="space-y-0.5">
              {items.map((n) => {
                const active = n.href === "/staff" ? path === "/staff" : path.startsWith(n.href);
                return (
                  <li key={n.href}>
                    <Link href={n.href} aria-current={active ? "page" : undefined}
                      className={`block rounded-md px-3 py-2 text-sm font-medium focus:outline-none focus-visible:ring-2 focus-visible:ring-teal-600 ${active ? "bg-teal-50 text-teal-900" : "text-slate-700 hover:bg-slate-100"}`}>
                      {n.text}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </nav>
        </aside>
        <div className="flex min-w-0 flex-1 flex-col">
          <header className="flex flex-wrap items-center gap-3 border-b border-slate-200 bg-white px-4 py-2.5">
            <button className="rounded p-1.5 text-slate-700 hover:bg-slate-100 md:hidden" aria-label="Toggle navigation" aria-expanded={navOpen} onClick={() => setNavOpen(!navOpen)}>☰</button>
            {ctx.hotels.length > 1 ? (
              <select aria-label="Hotel" value={ctx.hotel.id} onChange={(e) => ctx.setHotelId(e.target.value)} className="rounded-md border border-slate-300 px-2 py-1 text-sm">
                {ctx.hotels.map((h) => <option key={h.id} value={h.id}>{h.name}</option>)}
              </select>
            ) : <span className="text-sm font-semibold text-slate-900">{ctx.hotel.name}</span>}
            <div className="ml-auto flex items-center gap-2">
              <Bell hotelId={ctx.hotel.id} />
              <span className="hidden text-sm text-slate-600 sm:inline">{ctx.me.full_name} <Badge>{label(ctx.me.role)}</Badge></span>
              <Button variant="secondary" size="sm" onClick={signOut}>Sign out</Button>
            </div>
          </header>
          <main className="min-w-0 flex-1 p-4 md:p-6" id="main">{children}</main>
        </div>
      </div>
    </StaffContext.Provider>
  );
}
