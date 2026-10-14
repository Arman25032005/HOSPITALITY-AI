export const dt = (s?: string | null) =>
  s ? new Date(s).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "—";
export const day = (s?: string | null) =>
  s ? new Date(`${s}T00:00:00`).toLocaleDateString(undefined, { dateStyle: "medium" }) : "—";
export const label = (s: string) => s.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
export const money = (v: string | number, cur = "USD") =>
  new Intl.NumberFormat(undefined, { style: "currency", currency: cur }).format(Number(v));
export const iso = (d: Date) => d.toISOString().slice(0, 10);
export const today = () => iso(new Date());

// Mirrors the server's ticket workflow for display only; the API enforces the real rules.
export const TRANSITIONS: Record<string, string[]> = {
  open: ["assigned", "in_progress", "escalated", "closed"],
  assigned: ["open", "in_progress", "escalated"],
  in_progress: ["assigned", "resolved", "escalated"],
  escalated: ["assigned", "in_progress", "resolved"],
  resolved: ["in_progress", "closed"],
  closed: [],
};
