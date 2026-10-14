import { NextRequest, NextResponse } from "next/server";

export const API_INTERNAL_URL = process.env.API_INTERNAL_URL ?? "http://localhost:8000";
export const SESSION_COOKIE = "hos_session";
export const CSRF_HEADER = "x-requested-with";
export const CSRF_VALUE = "hospitality";

export function sessionCookie(token: string) {
  return {
    name: SESSION_COOKIE,
    value: token,
    httpOnly: true,
    sameSite: "lax" as const,
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: 60 * 60 * 8,
  };
}

/** Seconds until a JWT expires, read from its (unverified) payload; the API remains the authority. */
export function secondsLeft(token: string): number {
  try {
    const payload = JSON.parse(Buffer.from(token.split(".")[1], "base64url").toString());
    return Number(payload.exp) - Math.floor(Date.now() / 1000);
  } catch {
    return 0;
  }
}

export function clientIp(req: NextRequest): string {
  return req.headers.get("x-forwarded-for")?.split(",")[0].trim() ?? "unknown";
}

export function csrfOk(req: NextRequest): boolean {
  return req.method === "GET" || req.headers.get(CSRF_HEADER) === CSRF_VALUE;
}

export async function forward(
  req: NextRequest,
  upstreamPath: string,
  headers: Record<string, string>,
): Promise<NextResponse> {
  const url = `${API_INTERNAL_URL}${upstreamPath}${req.nextUrl.search}`;
  const init: RequestInit = { method: req.method, headers, cache: "no-store" };
  if (!["GET", "HEAD"].includes(req.method)) {
    const body = await req.arrayBuffer();
    if (body.byteLength) init.body = body;
    const ct = req.headers.get("content-type");
    if (ct) headers["content-type"] = ct;
  }
  let upstream: Response;
  try {
    upstream = await fetch(url, init);
  } catch {
    return NextResponse.json({ detail: "The service is temporarily unavailable" }, { status: 502 });
  }
  const out = new NextResponse(upstream.status === 204 ? null : await upstream.arrayBuffer(), {
    status: upstream.status,
  });
  const ct = upstream.headers.get("content-type");
  if (ct) out.headers.set("content-type", ct);
  const retry = upstream.headers.get("retry-after");
  if (retry) out.headers.set("retry-after", retry);
  return out;
}
