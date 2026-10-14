import { NextRequest, NextResponse } from "next/server";
import { API_INTERNAL_URL, SESSION_COOKIE, csrfOk, sessionCookie } from "@/lib/server";

export const dynamic = "force-dynamic";

/** Changing a password revokes every session server-side, so this route also swaps in the new
 *  token the API returns; otherwise the user would be signed out by their own change. */
export async function POST(req: NextRequest) {
  if (!csrfOk(req)) return NextResponse.json({ detail: "Forbidden" }, { status: 403 });
  const token = req.cookies.get(SESSION_COOKIE)?.value;
  if (!token) return NextResponse.json({ detail: "Not authenticated" }, { status: 401 });
  let body: { current_password?: string; new_password?: string };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ detail: "Invalid request" }, { status: 400 });
  }
  const r = await fetch(`${API_INTERNAL_URL}/api/v1/auth/change-password`, {
    method: "POST",
    headers: { "content-type": "application/json", authorization: `Bearer ${token}` },
    body: JSON.stringify({ current_password: body.current_password ?? "", new_password: body.new_password ?? "" }),
    cache: "no-store",
  }).catch(() => null);
  if (!r) return NextResponse.json({ detail: "The service is temporarily unavailable" }, { status: 502 });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const d = (data as { detail?: unknown }).detail;
    return NextResponse.json({ detail: typeof d === "string" ? d : "Could not change the password" }, { status: r.status });
  }
  const out = NextResponse.json({ ok: true });
  out.cookies.set(sessionCookie((data as { access_token: string }).access_token));
  return out;
}
