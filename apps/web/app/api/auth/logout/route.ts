import { NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE, csrfOk } from "@/lib/server";

export async function POST(req: NextRequest) {
  if (!csrfOk(req)) return NextResponse.json({ detail: "Forbidden" }, { status: 403 });
  const out = NextResponse.json({ ok: true });
  out.cookies.delete(SESSION_COOKIE);
  return out;
}
