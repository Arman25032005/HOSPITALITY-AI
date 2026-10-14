import Link from "next/link";

export const dynamic = "force-dynamic";

interface PublicHotel { org_slug: string; hotel_slug: string; name: string }

async function hotels(): Promise<PublicHotel[] | null> {
  try {
    const base = process.env.API_INTERNAL_URL ?? "http://localhost:8000";
    const r = await fetch(`${base}/api/v1/public/hotels`, { cache: "no-store" });
    return r.ok ? await r.json() : null;
  } catch {
    return null;
  }
}

export default async function Home() {
  const list = await hotels();
  return (
    <main className="mx-auto min-h-screen max-w-3xl px-4 py-14">
      <p className="text-sm font-semibold text-teal-800">HospitalityOS AI</p>
      <h1 className="mt-2 text-3xl font-semibold text-slate-900">How can we help you today?</h1>
      <p className="mt-2 text-slate-600">
        Choose your hotel to chat with its concierge: ask about the hotel, book a room, or get help
        during your stay.
      </p>
      <section aria-labelledby="hotels" className="mt-8">
        <h2 id="hotels" className="text-sm font-semibold uppercase tracking-wide text-slate-500">Hotels</h2>
        {list === null && (
          <p role="alert" className="mt-3 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-900">
            We can't load the hotel list right now. Please try again shortly.
          </p>
        )}
        {list?.length === 0 && <p className="mt-3 text-slate-600">No hotels are available yet.</p>}
        <ul className="mt-3 grid gap-3 sm:grid-cols-2">
          {list?.map((h) => (
            <li key={`${h.org_slug}/${h.hotel_slug}`}>
              <Link href={`/chat/${h.org_slug}/${h.hotel_slug}`}
                className="block rounded-lg border border-slate-200 bg-white p-5 shadow-sm transition hover:border-teal-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-teal-600">
                <span className="block text-base font-semibold text-slate-900">{h.name}</span>
                <span className="mt-1 block text-sm text-teal-800">Chat with the concierge →</span>
              </Link>
            </li>
          ))}
        </ul>
      </section>
      <p className="mt-12 text-sm text-slate-500">
        Hotel staff? <Link href="/login" className="font-medium text-teal-800 underline">Sign in to the dashboard</Link>
      </p>
    </main>
  );
}
