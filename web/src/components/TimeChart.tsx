import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { Status } from "../lib/api";
import { STATUS_META, StatusBadge, fmt } from "./ui";

export type ChartPoint = { t: number; date: string; values: Record<string, number | null>; status?: Status };
export type ChartSeries = { key: string; label: string; colour: string; fill?: boolean; format?: (v: number) => string };

type Props = {
  data: ChartPoint[];
  series: ChartSeries[];
  yDomain?: [number, number];
  yTicks?: number[];
  yFormat?: (v: number) => string;
  height?: number;
  bands?: { from: number; to: number; label: string }[];
  events?: { t: number; label: string }[];
  showStatusMarkers?: boolean;
  ariaLabel: string;
};

const PAD = { top: 14, right: 14, bottom: 26, left: 40 };

/** Hairline-grid SVG time series with a snapping crosshair, keyboard support and status markers. */
export function TimeChart({
  data,
  series,
  yDomain,
  yTicks,
  yFormat = (v) => v.toFixed(1),
  height = 220,
  bands = [],
  events = [],
  showStatusMarkers = true,
  ariaLabel,
}: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(640);
  const [hover, setHover] = useState<number | null>(null);
  const gid = useId().replace(/:/g, "");

  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver(([e]) => setWidth(Math.max(280, e.contentRect.width)));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);

  const { x, y, ticksX, domainY, ticks } = useMemo(() => {
    const t0 = data.length ? data[0].t : 0;
    const t1 = data.length ? data[data.length - 1].t : 1;
    const vals = data.flatMap((d) => series.map((s) => d.values[s.key])).filter((v): v is number => v !== null && v !== undefined);
    const dom: [number, number] = yDomain ?? [Math.min(0, ...vals), Math.max(1, ...vals)];
    const x = (t: number) => PAD.left + ((t - t0) / Math.max(1, t1 - t0)) * (width - PAD.left - PAD.right);
    const y = (v: number) => PAD.top + (1 - (v - dom[0]) / (dom[1] - dom[0])) * (height - PAD.top - PAD.bottom);
    const years: number[] = [];
    const y0 = new Date(t0 * 1000).getUTCFullYear();
    const y1 = new Date(t1 * 1000).getUTCFullYear();
    const step = Math.max(1, Math.ceil((y1 - y0 + 1) / Math.max(2, Math.floor(width / 90))));
    for (let yr = y0; yr <= y1 + 1; yr += step) years.push(Date.UTC(yr, 0, 1) / 1000);
    const ticksX = years.filter((t) => t >= t0 && t <= t1);
    const ticks = yTicks ?? [dom[0], (dom[0] + dom[1]) / 2, dom[1]];
    return { x, y, ticksX, domainY: dom, ticks };
  }, [data, series, width, height, yDomain, yTicks]);

  const paths = useMemo(
    () =>
      series.map((s) => {
        let d = "";
        let pen = false;
        let area = "";
        let first: number | null = null;
        let last: number | null = null;
        for (const p of data) {
          const v = p.values[s.key];
          if (v === null || v === undefined) continue;
          const px = x(p.t);
          const py = y(v);
          d += `${pen ? "L" : "M"}${px.toFixed(1)},${py.toFixed(1)}`;
          area += `${pen ? "L" : "M"}${px.toFixed(1)},${py.toFixed(1)}`;
          if (first === null) first = px;
          last = px;
          pen = true;
        }
        const base = y(Math.max(domainY[0], 0));
        if (first !== null && last !== null) area += `L${last.toFixed(1)},${base.toFixed(1)}L${first.toFixed(1)},${base.toFixed(1)}Z`;
        return { s, d, area };
      }),
    [data, series, x, y, domainY],
  );

  const nearest = (clientX: number) => {
    const rect = ref.current!.getBoundingClientRect();
    const px = clientX - rect.left;
    let best = 0;
    let dist = Infinity;
    data.forEach((p, i) => {
      const dd = Math.abs(x(p.t) - px);
      if (dd < dist && series.some((s) => p.values[s.key] !== null && p.values[s.key] !== undefined)) {
        dist = dd;
        best = i;
      }
    });
    return best;
  };

  const hp = hover !== null ? data[hover] : null;
  const tipLeft = hp ? Math.min(Math.max(x(hp.t) + 12, 8), width - 196) : 0;

  return (
    <div
      ref={ref}
      className="relative w-full select-none outline-none"
      tabIndex={0}
      role="img"
      aria-label={ariaLabel}
      onPointerMove={(e) => data.length && setHover(nearest(e.clientX))}
      onPointerLeave={() => setHover(null)}
      onBlur={() => setHover(null)}
      onKeyDown={(e) => {
        if (!data.length) return;
        if (e.key === "ArrowRight") setHover((h) => Math.min(data.length - 1, (h ?? -1) + 1));
        if (e.key === "ArrowLeft") setHover((h) => Math.max(0, (h ?? data.length) - 1));
      }}
    >
      <svg width={width} height={height} className="block">
        <defs>
          {series
            .filter((s) => s.fill)
            .map((s) => (
              <linearGradient key={s.key} id={`${gid}-${s.key}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={s.colour} stopOpacity={0.2} />
                <stop offset="100%" stopColor={s.colour} stopOpacity={0} />
              </linearGradient>
            ))}
        </defs>
        {bands.map((b) => (
          <g key={b.label}>
            <rect x={x(b.from)} y={PAD.top} width={Math.max(0, x(b.to) - x(b.from))} height={height - PAD.top - PAD.bottom} fill="var(--paper-2)" />
            <text x={x(b.from) + 6} y={PAD.top + 12} className="fill-[var(--muted)] font-mono text-[10px] uppercase tracking-[0.08em]">
              {b.label}
            </text>
          </g>
        ))}
        {ticks.map((v) => (
          <g key={v}>
            <line x1={PAD.left} x2={width - PAD.right} y1={y(v)} y2={y(v)} stroke="var(--hair)" />
            <text x={PAD.left - 8} y={y(v) + 3.5} textAnchor="end" className="fill-[var(--muted)] text-[10.5px] [font-variant-numeric:tabular-nums]">
              {yFormat(v)}
            </text>
          </g>
        ))}
        {ticksX.map((t) => (
          <text key={t} x={x(t)} y={height - 8} textAnchor="middle" className="fill-[var(--muted)] text-[10.5px] [font-variant-numeric:tabular-nums]">
            {new Date(t * 1000).getUTCFullYear()}
          </text>
        ))}
        {events.map((ev) => (
          <g key={ev.label}>
            <line x1={x(ev.t)} x2={x(ev.t)} y1={PAD.top} y2={height - PAD.bottom} stroke="var(--accent)" strokeWidth={1} />
            <text x={x(ev.t) + 5} y={height - PAD.bottom - 6} className="fill-[var(--accent)] font-display text-[11px] font-semibold">
              {ev.label}
            </text>
          </g>
        ))}
        {paths.map(({ s, area }) => s.fill && <path key={`a-${s.key}`} d={area} fill={`url(#${gid}-${s.key})`} />)}
        {paths.map(({ s, d }) => (
          <path key={s.key} d={d} fill="none" stroke={s.colour} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        ))}
        {showStatusMarkers &&
          data.map((p) =>
            p.status === "reversal_suspected" || p.status === "watch" ? (
              (() => {
                const v = p.values[series[0].key];
                if (v === null || v === undefined) return null;
                return (
                  <circle
                    key={`m-${p.t}`}
                    cx={x(p.t)}
                    cy={y(v)}
                    r={4}
                    fill={STATUS_META[p.status].colour}
                    stroke="var(--surface)"
                    strokeWidth={2}
                  />
                );
              })()
            ) : null,
          )}
        {hp && (
          <g>
            <line x1={x(hp.t)} x2={x(hp.t)} y1={PAD.top} y2={height - PAD.bottom} stroke="var(--hair-strong)" />
            {series.map((s) => {
              const v = hp.values[s.key];
              return v === null || v === undefined ? null : (
                <circle key={s.key} cx={x(hp.t)} cy={y(v)} r={4.5} fill={s.colour} stroke="var(--surface)" strokeWidth={2} />
              );
            })}
          </g>
        )}
      </svg>
      {hp && (
        <div className="chart-tip" style={{ left: tipLeft, top: 8 }}>
          <p className="mb-1.5 text-[11.5px] text-muted">{fmt.date(hp.date)}</p>
          {series.map((s) => {
            const v = hp.values[s.key];
            return (
              <div key={s.key} className="flex items-center justify-between gap-3 py-0.5">
                <span className="flex items-center gap-1.5">
                  <span className="inline-block h-[2px] w-3 rounded" style={{ background: s.colour }} />
                  {s.label}
                </span>
                <strong className="font-display text-ink [font-variant-numeric:tabular-nums]">
                  {v === null || v === undefined ? "no view" : (s.format ?? yFormat)(v)}
                </strong>
              </div>
            );
          })}
          {hp.status && (
            <div className="mt-1.5">
              <StatusBadge status={hp.status} size="sm" />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function Legend({ items }: { items: { label: string; colour: string; kind?: "line" | "dot" }[] }) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[12.5px] text-ink-2">
      {items.map((it) => (
        <span key={it.label} className="inline-flex items-center gap-1.5">
          {it.kind === "dot" ? (
            <span className="status-dot" style={{ background: it.colour }} />
          ) : (
            <span className="inline-block h-[2px] w-4 rounded" style={{ background: it.colour }} />
          )}
          {it.label}
        </span>
      ))}
    </div>
  );
}
