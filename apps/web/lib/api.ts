export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

const HEADERS = { "content-type": "application/json", "x-requested-with": "hospitality" };

function detailOf(data: unknown, fallback: string): string {
  const d = (data as { detail?: unknown })?.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d) && d[0]?.msg) return String(d[0].msg);
  return fallback;
}

async function call<T>(base: string, path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${base}/${path}`, { ...init, cache: "no-store" });
  if (res.status === 204) return undefined as T;
  let data: unknown = null;
  try {
    data = await res.json();
  } catch {
    /* non-JSON error body */
  }
  if (!res.ok) {
    if (res.status === 401 && base === "/api/bff" && typeof window !== "undefined") {
      window.location.href = `/login?next=${encodeURIComponent(window.location.pathname)}`;
    }
    throw new ApiError(res.status, detailOf(data, `Request failed (${res.status})`));
  }
  return data as T;
}

/** Staff API through the same-origin proxy (session cookie is httpOnly; not readable here). */
export function staff<T>(path: string, init: { method?: string; body?: unknown } = {}): Promise<T> {
  return call<T>("/api/bff", path, {
    method: init.method ?? "GET",
    headers: HEADERS,
    body: init.body === undefined ? undefined : JSON.stringify(init.body),
  });
}

export function staffUpload<T>(path: string, form: FormData): Promise<T> {
  return call<T>("/api/bff", path, {
    method: "POST",
    headers: { "x-requested-with": "hospitality" },
    body: form,
  });
}

export function guest<T>(
  path: string, token: string | null, init: { method?: string; body?: unknown } = {},
): Promise<T> {
  return call<T>("/api/guest", path, {
    method: init.method ?? "GET",
    headers: token ? { ...HEADERS, authorization: `Bearer ${token}` } : HEADERS,
    body: init.body === undefined ? undefined : JSON.stringify(init.body),
  });
}

export function qs(params: Record<string, string | number | boolean | undefined | null>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "" && v !== false) p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : "";
}
