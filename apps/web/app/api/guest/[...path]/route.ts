import { NextRequest, NextResponse } from "next/server";
import { clientIp, csrfOk, forward } from "@/lib/server";

export const dynamic = "force-dynamic";

// Anonymous guest chat only; the guest token travels in the Authorization header.
const ALLOWED = [
  /^public\/hotels$/, /^chat\/sessions$/, /^chat\/messages$/, /^chat\/verify$/,
  /^chat\/messages\/[0-9a-f-]{36}\/feedback$/,
];

async function handler(req: NextRequest, { params }: { params: { path: string[] } }) {
  const path = params.path.join("/");
  if (!ALLOWED.some((rx) => rx.test(path))) {
    return NextResponse.json({ detail: "Not found" }, { status: 404 });
  }
  if (!csrfOk(req)) return NextResponse.json({ detail: "Forbidden" }, { status: 403 });
  const headers: Record<string, string> = { "x-forwarded-for": clientIp(req) };
  const auth = req.headers.get("authorization");
  if (auth) headers.authorization = auth;
  return forward(req, `/api/v1/${path}`, headers);
}

export { handler as GET, handler as POST };
