"use client";

import { ReactNode, useEffect, useRef } from "react";
import { ApiError } from "@/lib/api";

const cx = (...c: (string | false | undefined)[]) => c.filter(Boolean).join(" ");

export function Button({
  variant = "primary", size = "md", className, ...p
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "danger" | "ghost"; size?: "sm" | "md";
}) {
  const v = {
    primary: "bg-teal-700 text-white hover:bg-teal-800 disabled:bg-teal-700/50",
    secondary: "bg-white text-slate-800 border border-slate-300 hover:bg-slate-50 disabled:opacity-50",
    danger: "bg-red-700 text-white hover:bg-red-800 disabled:bg-red-700/50",
    ghost: "text-slate-700 hover:bg-slate-100 disabled:opacity-50",
  }[variant];
  return (
    <button
      {...p}
      className={cx(
        "inline-flex items-center justify-center rounded-md font-medium focus:outline-none focus-visible:ring-2 focus-visible:ring-teal-600 focus-visible:ring-offset-1 disabled:cursor-not-allowed",
        size === "sm" ? "px-2.5 py-1 text-sm" : "px-3.5 py-2 text-sm", v, className,
      )}
    />
  );
}

const TONES: Record<string, string> = {
  slate: "bg-slate-100 text-slate-700 ring-slate-200",
  green: "bg-emerald-50 text-emerald-800 ring-emerald-200",
  amber: "bg-amber-50 text-amber-900 ring-amber-200",
  red: "bg-red-50 text-red-800 ring-red-200",
  blue: "bg-sky-50 text-sky-800 ring-sky-200",
  purple: "bg-violet-50 text-violet-800 ring-violet-200",
};
export function Badge({ tone = "slate", children }: { tone?: keyof typeof TONES; children: ReactNode }) {
  return <span className={cx("inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset", TONES[tone])}>{children}</span>;
}

export const statusTone = (s: string) =>
  ({ open: "blue", assigned: "purple", in_progress: "amber", escalated: "red", resolved: "green",
     closed: "slate", confirmed: "blue", checked_in: "green", cancelled: "slate", pending: "amber",
     approved: "green", denied: "red", active: "green", handed_off: "red" } as Record<string, string>)[s] ?? "slate";
export const priorityTone = (p: string) =>
  ({ low: "slate", normal: "blue", high: "amber", urgent: "red" } as Record<string, string>)[p] ?? "slate";

export function Card({ title, actions, children, className }: {
  title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string;
}) {
  return (
    <section className={cx("rounded-lg border border-slate-200 bg-white shadow-sm", className)}>
      {(title || actions) && (
        <header className="flex items-center justify-between gap-2 border-b border-slate-100 px-4 py-3">
          <h2 className="text-sm font-semibold text-slate-900">{title}</h2>
          {actions}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function Field({ label, htmlFor, hint, children }: {
  label: string; htmlFor?: string; hint?: string; children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={htmlFor} className="text-sm font-medium text-slate-700">{label}</label>
      {children}
      {hint && <p className="text-xs text-slate-500">{hint}</p>}
    </div>
  );
}
const inputCls = "w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 placeholder:text-slate-500 focus:border-teal-600 focus:outline-none focus:ring-1 focus:ring-teal-600";
export const Input = (p: React.InputHTMLAttributes<HTMLInputElement>) => <input {...p} className={cx(inputCls, p.className)} />;
export const Select = (p: React.SelectHTMLAttributes<HTMLSelectElement>) => <select {...p} className={cx(inputCls, p.className)} />;
export const Textarea = (p: React.TextareaHTMLAttributes<HTMLTextAreaElement>) => <textarea {...p} className={cx(inputCls, p.className)} />;

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <div role="status" className="flex items-center gap-2 text-sm text-slate-500">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-teal-700" aria-hidden />
      {label}…
    </div>
  );
}

/** One component for every non-happy-path state so screens stay consistent. */
export function State({ kind, title, detail, action }: {
  kind: "loading" | "empty" | "error" | "denied"; title?: string; detail?: string; action?: ReactNode;
}) {
  if (kind === "loading") return <div className="py-10 text-center"><Spinner /></div>;
  const copy = {
    empty: ["Nothing here yet", "No records match."],
    error: ["Something went wrong", "Please try again."],
    denied: ["You don't have access", "Your role does not include this area."],
  }[kind];
  return (
    <div role={kind === "error" ? "alert" : undefined} className="rounded-lg border border-dashed border-slate-300 bg-white px-6 py-10 text-center">
      <p className="text-sm font-semibold text-slate-800">{title ?? copy[0]}</p>
      <p className="mt-1 text-sm text-slate-500">{detail ?? copy[1]}</p>
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function ErrorState({ error, retry }: { error: unknown; retry?: () => void }) {
  if (error instanceof ApiError && error.status === 403) return <State kind="denied" detail={error.message} />;
  const msg = error instanceof Error ? error.message : "Unknown error";
  return <State kind="error" detail={msg} action={retry && <Button variant="secondary" onClick={retry}>Try again</Button>} />;
}

export function Notice({ tone, children }: { tone: "success" | "error" | "info"; children: ReactNode }) {
  const c = { success: "border-emerald-200 bg-emerald-50 text-emerald-900", error: "border-red-200 bg-red-50 text-red-900", info: "border-sky-200 bg-sky-50 text-sky-900" }[tone];
  return <div role={tone === "error" ? "alert" : "status"} className={cx("rounded-md border px-3 py-2 text-sm", c)}>{children}</div>;
}

export function Modal({ title, onClose, children, wide }: {
  title: string; onClose: () => void; children: ReactNode; wide?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    ref.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "Tab" && ref.current) {
        const f = ref.current.querySelectorAll<HTMLElement>("button,[href],input,select,textarea,[tabindex]:not([tabindex='-1'])");
        if (!f.length) return;
        const first = f[0], last = f[f.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    };
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("keydown", onKey); prev?.focus(); };
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-40 flex items-end justify-center bg-slate-900/40 p-0 sm:items-center sm:p-4" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={ref} role="dialog" aria-modal="true" aria-label={title} tabIndex={-1}
        className={cx("max-h-[90vh] w-full overflow-y-auto rounded-t-xl bg-white shadow-xl focus:outline-none sm:rounded-xl", wide ? "sm:max-w-3xl" : "sm:max-w-lg")}>
        <header className="flex items-center justify-between border-b border-slate-100 px-5 py-3">
          <h2 className="text-base font-semibold text-slate-900">{title}</h2>
          <button onClick={onClose} aria-label="Close" className="rounded p-1 text-slate-500 hover:bg-slate-100 focus-visible:ring-2 focus-visible:ring-teal-600">✕</button>
        </header>
        <div className="p-5">{children}</div>
      </div>
    </div>
  );
}

export function Table({ head, children, caption }: { head: string[]; children: ReactNode; caption: string }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
      <table className="min-w-full divide-y divide-slate-200 text-sm">
        <caption className="sr-only">{caption}</caption>
        <thead className="bg-slate-50">
          <tr>{head.map((h) => <th key={h} scope="col" className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">{h}</th>)}</tr>
        </thead>
        <tbody className="divide-y divide-slate-100">{children}</tbody>
      </table>
    </div>
  );
}
