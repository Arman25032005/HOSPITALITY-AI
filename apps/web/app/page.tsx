import Link from "next/link";

export default function Home() {
  return (
    <main className="mx-auto flex min-h-screen max-w-xl flex-col justify-center gap-6 px-6">
      <h1 className="text-3xl font-semibold">HospitalityOS AI</h1>
      <p className="text-slate-600">
        Hotel operations platform. Staff can sign in to the operations dashboard. The guest
        concierge chat is not yet available in this build.
      </p>
      <Link
        href="/login"
        className="w-fit rounded-md bg-slate-900 px-4 py-2 text-white hover:bg-slate-700 focus:outline-none focus:ring-2 focus:ring-slate-500"
      >
        Staff sign in
      </Link>
    </main>
  );
}
