import { NextRequest, NextResponse } from "next/server";
import { API_INTERNAL_URL, clientIp, csrfOk, sessionCookie } from "@/lib/server";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  if (!csrfOk(req)) return NextResponse.json({ detail: "Forbidden" }, { status: 403 });
  let body: { email?: string; password?: string };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ detail: "Invalid request" }, { status: 400 });
  }
  const r = await fetch(`${API_INTERNAL_URL}/api/v1/auth/login`, {
    method: "POST",
    headers: { "content-type": "application/json", "x-forwarded-for": clientIp(req) },
    body: JSON.stringify({ email: body.email ?? "", password: body.password ?? "" }),
    cache: "no-store",
  }).catch(() => null);
  if (!r) return NextResponse.json({ detail: "The service is temporarily unavailable" }, { status: 502 });
  if (!r.ok) {
    const status = r.status === 429 ? 429 : r.status === 401 ? 401 : 400;
    const detail = status === 401 ? "Invalid email or password"
      : status === 429 ? "Too many attempts. Please wait and try again." : "Sign-in failed";
    return NextResponse.json({ detail }, { status });
  }
  const { access_token } = await r.json();
  const out = NextResponse.json({ ok: true });
  out.cookies.set(sessionCookie(access_token));
  return out;
}
