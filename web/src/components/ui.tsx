import { AlertTriangle, CheckCircle2, CircleDashed, Flame, RefreshCw } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "../kit";
import type { Status } from "../lib/api";

export const STATUS_META: Record<Status, { label: string; colour: string; icon: ReactNode; short: string }> = {
  healthy: { label: "Healthy", short: "Healthy", colour: "var(--st-healthy)", icon: <CheckCircle2 size={13} strokeWidth={1.9} /> },
  watch: { label: "Watch", short: "Watch", colour: "var(--st-watch)", icon: <AlertTriangle size={13} strokeWidth={1.9} /> },
  reversal_suspected: {
    label: "Reversal suspected",
    short: "Reversal",
    colour: "var(--st-reversal)",
    icon: <Flame size={13} strokeWidth={1.9} />,
  },
  insufficient_data: { label: "No clear view", short: "No view", colour: "var(--st-none)", icon: <CircleDashed size={13} strokeWidth={1.9} /> },
  unmonitored: { label: "Unmonitored", short: "Unmonitored", colour: "var(--st-none)", icon: <CircleDashed size={13} strokeWidth={1.9} /> },
};

/** Badge-styled status pill: colour, icon and words together, matching the kit's Badge. */
export function StatusBadge({ status, size = "md" }: { status: Status; size?: "sm" | "md" }) {
  const m = STATUS_META[status];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 whitespace-nowrap rounded-full font-semibold",
        size === "sm" ? "px-2 py-0.5 text-[11.5px]" : "px-2.5 py-1 text-[12px]",
      )}
      style={{ color: m.colour, background: `color-mix(in oklab, ${m.colour} 12%, transparent)` }}
    >
      {m.icon}
      {size === "sm" ? m.short : m.label}
    </span>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden className={cn("skeleton", className)} />;
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="flex flex-col items-start gap-3 rounded-[16px] border border-hair bg-paper-2 p-5">
      <p className="font-display text-[15px] font-semibold text-ink">We could not load this.</p>
      <p className="text-[13.5px] text-ink-2">{message}</p>
      {onRetry && (
        <button
          onClick={onRetry}
          className="inline-flex items-center gap-1.5 rounded-full border border-hair bg-surface px-3.5 py-1.5 font-display text-[13px] font-semibold text-ink shadow-[var(--shadow-soft)] transition hover:-translate-y-px"
        >
          <RefreshCw size={14} strokeWidth={1.8} /> Try again
        </button>
      )}
    </div>
  );
}

export function Empty({ title, body }: { title: string; body: ReactNode }) {
  return (
    <div className="rounded-[16px] border border-dashed border-[var(--hair-strong)] p-6 text-center">
      <p className="font-display text-[15px] font-semibold text-ink">{title}</p>
      <p className="mt-1 text-[13.5px] text-muted">{body}</p>
    </div>
  );
}

export function KV({ k, v, mono }: { k: ReactNode; v: ReactNode; mono?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-2.5 text-[13.5px]">
      <span className="shrink-0 text-muted">{k}</span>
      <span className={cn("min-w-0 text-right text-ink", mono && "mono-hash")}>{v}</span>
    </div>
  );
}

export const fmt = {
  pct: (frac: string | number | null | undefined, digits = 1) =>
    frac === null || frac === undefined ? "n/a" : `${(Number(frac) * 100).toFixed(digits)}%`,
  bps: (bps: number) => `${(bps / 100).toFixed(1)}%`,
  ha: (v: string | number | null | undefined) => (v === null || v === undefined ? "n/a" : `${Number(v).toFixed(1)} ha`),
  int: (n: number) => n.toLocaleString("en-US"),
  short: (h: string, n = 6) => (h.length > 2 * n + 2 ? `${h.slice(0, n + 2)}...${h.slice(-n)}` : h),
  date: (iso: string) =>
    new Date(iso.length === 10 ? `${iso}T00:00:00Z` : iso).toLocaleDateString("en-GB", {
      day: "numeric",
      month: "short",
      year: "numeric",
      timeZone: "UTC",
    }),
  unix: (s: number) => fmt.date(new Date(s * 1000).toISOString()),
};
