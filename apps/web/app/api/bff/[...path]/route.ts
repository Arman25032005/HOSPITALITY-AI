import { NextRequest, NextResponse } from "next/server";
import {
  csrfOk, forward, API_INTERNAL_URL, SESSION_COOKIE, secondsLeft, sessionCookie,
} from "@/lib/server";

export const dynamic = "force-dynamic";

// Only staff API areas are reachable through the browser; auth and webhooks are not proxied.
// Admin user management is proxied too; the API enforces the admin-only rules.
const ALLOWED = /^(hotels(\/.*)?|auth\/me|users|users\/[0-9a-f-]{36}|users\/[0-9a-f-]{36}\/reset-password)$/;

async function handler(req: NextRequest, { params }: { params: { path: string[] } }) {
  const path = params.path.join("/");
  if (!ALLOWED.test(path) || path.includes("..")) {
    return NextResponse.json({ detail: "Not found" }, { status: 404 });
  }
  if (!csrfOk(req)) return NextResponse.json({ detail: "Forbidden" }, { status: 403 });
  const token = req.cookies.get(SESSION_COOKIE)?.value;
  if (!token) return NextResponse.json({ detail: "Not authenticated" }, { status: 401 });

  let fresh: string | null = null;
  if (secondsLeft(token) < 600) {
    const r = await fetch(`${API_INTERNAL_URL}/api/v1/auth/refresh`, {
      method: "POST", headers: { authorization: `Bearer ${token}` }, cache: "no-store",
    }).catch(() => null);
    if (r?.ok) fresh = (await r.json()).access_token;
  }
  const res = await forward(req, `/api/v1/${path}`, { authorization: `Bearer ${fresh ?? token}` });
  if (fresh) res.cookies.set(sessionCookie(fresh));
  if (res.status === 401) res.cookies.delete(SESSION_COOKIE);
  return res;
}

export { handler as GET, handler as POST, handler as PUT, handler as PATCH, handler as DELETE };
