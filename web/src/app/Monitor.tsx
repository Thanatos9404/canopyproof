import { Sparkles } from "lucide-react";
import { useMemo, useState } from "react";
import { Badge, Panel, Segmented, cn } from "../kit";
import { CellMap, type MapOverlay } from "../components/CellMap";
import { Legend, TimeChart, type ChartPoint } from "../components/TimeChart";
import { Empty, ErrorState, KV, Skeleton, STATUS_META, StatusBadge, fmt } from "../components/ui";
import { useApi, type SiteDetail, type SiteSummary, type Status } from "../lib/api";
import { go, type AppData } from "./ProductApp";

type View = "latest" | "baseline" | "loss";
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const unix = (d: string) => Date.parse(`${d}T00:00:00Z`) / 1000;

export function Monitor({ data, siteId }: { data: AppData; siteId?: string }) {
  const sites = data.sites.data?.sites ?? [];
  const active = siteId ?? "KE-FIRE-01";
  const detail = useApi<SiteDetail>(`/api/sites/${encodeURIComponent(active)}`);
  const [view, setView] = useState<View>("loss");

  if (data.sites.error) return <ErrorState message={data.sites.error.message} onRetry={data.sites.retry} />;
  return (
    <div className="space-y-4">
      <CellPicker sites={sites} active={active} />
      {detail.error ? (
        <ErrorState message={detail.error.message} onRetry={detail.retry} />
      ) : !detail.data ? (
        <div className="grid gap-4 lg:grid-cols-12">
          <Skeleton className="h-[420px] lg:col-span-7" />
          <Skeleton className="h-[420px] lg:col-span-5" />
        </div>
      ) : (
        <CellDetail d={detail.data} view={view} setView={setView} />
      )}
    </div>
  );
}

function CellPicker({ sites, active }: { sites: SiteSummary[]; active: string }) {
  if (!sites.length) return <Skeleton className="h-24" />;
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      {sites.map((s) => {
        const st = (s.latest?.status ?? "insufficient_data") as Status;
        return (
          <button
            key={s.site_id}
            onClick={() => go("monitor", s.site_id)}
            aria-pressed={s.site_id === active}
            className={cn(
              "group flex items-center gap-3 rounded-[18px] border bg-surface p-2.5 text-left shadow-[var(--shadow-soft)] transition hover:-translate-y-0.5",
              s.site_id === active ? "border-[var(--accent)]" : "border-hair",
            )}
          >
            {s.chip ? (
              <img src={s.chip.url} alt="" className="size-14 shrink-0 rounded-xl object-cover" />
            ) : (
              <span className="size-14 shrink-0 rounded-xl bg-paper-2" />
            )}
            <span className="min-w-0">
              <span className="block truncate font-display text-[13.5px] font-semibold text-ink">{s.name}</span>
              <span className="mt-1 block">
                <StatusBadge status={st} size="sm" />
              </span>
            </span>
          </button>
        );
      })}
    </div>
  );
}

function CellDetail({ d, view, setView }: { d: SiteDetail; view: View; setView: (v: View) => void }) {
  const measured = d.series.filter((r) => r.status !== "insufficient_data");
  const status = (d.latest?.status ?? "insufficient_data") as Status;

  const overlays: MapOverlay[] = useMemo(() => {
    const out: MapOverlay[] = [];
    const base = view === "baseline" ? d.chip_first : d.chip;
    if (base) out.push({ url: base.url, bounds: base.bounds, pixelated: true });
    if (view === "loss" && d.changemap.confirmed_pixels > 0) out.push({ url: d.changemap.url, bounds: d.changemap.bounds, opacity: 0.85, pixelated: true });
    return out;
  }, [d, view]);

  const points: ChartPoint[] = measured.map((r) => ({
    t: r.t,
    date: r.date,
    status: r.status,
    values: { ndvi: r.ndvi === null ? null : Number(r.ndvi), nbr: r.nbr === null ? null : Number(r.nbr), loss: r.loss === null ? null : Number(r.loss) },
  }));
  const cb = d.project.credit_batch;
  const bands = [{ from: unix(cb.baseline_start), to: unix(cb.baseline_end), label: "baseline" }];
  const events = d.first_reversal ? [{ t: d.first_reversal.t, label: "reversal suspected" }] : [];
  const v = d.validation;

  return (
    <>
      <div className="grid gap-4 lg:grid-cols-12">
        <Panel
          className="lg:col-span-7"
          title={d.name}
          description={`${d.project.name} · ${d.area_ha} ha cell · ${d.role}`}
        >
          <div className="mb-3 flex justify-end">
            <Segmented
              id="cell-view"
              value={view}
              onChange={setView}
              options={[
                { value: "latest", label: "Latest" },
                { value: "baseline", label: "Baseline" },
                { value: "loss", label: "Loss" },
              ]}
            />
          </div>
          <CellMap
            features={[{ id: d.site_id, label: d.name, geometry: d.geometry, status }]}
            selected={d.site_id}
            overlays={overlays}
            fit="selected"
            height={380}
            label={`Map of ${d.name}`}
          />
          <p className="mt-3 text-[12.5px] text-muted">
            {view === "loss"
              ? `Terracotta pixels: forest with loss confirmed by two clear looks (${d.changemap.confirmed_pixels} of ${d.changemap.forest_pixels} forest pixels at 20 m). `
              : ""}
            Sentinel-2 true colour {view === "baseline" ? d.chip_first?.date : d.chip?.date}, contains modified Copernicus Sentinel data.
          </p>
        </Panel>
        <Panel className="lg:col-span-5" title="Integrity" action={<StatusBadge status={status} />}>
          <div className="divide-y divide-[var(--hair)]">
            <KV k="Latest clear look" v={d.latest ? fmt.date(d.latest.date) : "n/a"} />
            <KV k="Baseline forest in cell" v={fmt.ha(d.forest_ha)} />
            <KV k="Confirmed loss since baseline" v={`${fmt.ha(d.confirmed_loss_ha)} (${fmt.pct(d.latest?.loss)})`} />
            <KV k="First reversal suspected" v={d.first_reversal ? fmt.date(d.first_reversal.date) : "never"} />
            <KV k="Clear looks compared" v={`${measured.length} of ${d.records} anchored`} />
            <KV k="Monitoring season" v={`${MONTHS[cb.monitoring_months[0] - 1]} to ${MONTHS[cb.monitoring_months[cb.monitoring_months.length - 1] - 1]}`} />
            <KV k="Hansen GFC agreement" v={v ? (v.precision !== null && v.recall !== null ? `precision ${fmt.pct(v.precision)}, recall ${fmt.pct(v.recall)}` : "no Hansen loss 2019-2024") : "not run"} />
          </div>
          {d.note && (
            <div className="mt-4 rounded-2xl bg-paper-2 p-4">
              <p className="flex items-center gap-1.5 t-eyebrow">
                {d.note.ai_generated && <Sparkles size={12} />} Integrity note · {d.note.generated_by}
              </p>
              <p className="mt-2 text-[13.5px] leading-relaxed text-ink-2">{d.note.text}</p>
              <p className="mt-2 text-[12px] text-muted">{d.note.disclaimer}</p>
            </div>
          )}
        </Panel>
      </div>

      <Panel
        title="Vegetation and burn signals"
        description="Cell means for every clear look. Dots mark looks the rules flagged; the shaded band is the baseline period."
        action={<Legend items={[{ label: "NDVI", colour: "var(--series-ndvi)" }, { label: "NBR", colour: "var(--series-nbr)" }]} />}
      >
        <TimeChart
          data={points}
          series={[
            { key: "ndvi", label: "NDVI", colour: "var(--series-ndvi)", fill: true },
            { key: "nbr", label: "NBR", colour: "var(--series-nbr)" },
          ]}
          yDomain={[-0.4, 1]}
          yTicks={[-0.4, 0, 0.5, 1]}
          yFormat={(n) => n.toFixed(1)}
          bands={bands}
          events={events}
          height={240}
          ariaLabel={`NDVI and NBR time series for ${d.name}`}
        />
        <p className="mt-2 text-[12px] text-muted">
          {Object.entries(STATUS_META)
            .filter(([k]) => k === "watch" || k === "reversal_suspected")
            .map(([, m]) => m.label)
            .join(" and ")}{" "}
          looks are marked on the NDVI line. A burn shows as NBR falling below zero.
        </p>
      </Panel>

      <div className="grid gap-4 lg:grid-cols-12">
        <Panel className="lg:col-span-7" title="Confirmed loss since the baseline" description="Share of the cell's baseline forest disturbed in two consecutive clear looks. It never decreases.">
          <TimeChart
            data={points}
            series={[{ key: "loss", label: "Confirmed loss", colour: "var(--st-reversal)", fill: true, format: (n) => `${(n * 100).toFixed(1)}%` }]}
            yDomain={[0, 1]}
            yTicks={[0, 0.05, 0.5, 1]}
            yFormat={(n) => `${Math.round(n * 100)}%`}
            events={events}
            showStatusMarkers={false}
            height={200}
            ariaLabel={`Confirmed forest loss over time for ${d.name}`}
          />
          <p className="mt-2 text-[12px] text-muted">Gridline at 5%: the reversal threshold registered for this credit batch.</p>
        </Panel>
        <Panel className="lg:col-span-5" title="Independent check: Hansen GFC" description="Annual Landsat-based tree-cover loss (UMD/Google, CC BY 4.0) in the same cell.">
          {v ? <HansenTable v={v} /> : <Empty title="Not run yet" body="Run `python -m canopyproof validate`." />}
        </Panel>
      </div>

      <Panel title="Anchored records" description="The latest measured looks. Each row is a signed record inside a monthly Merkle batch." action={<Badge>{d.records} total</Badge>}>
        <RecordsTable rows={measured.slice(-10).reverse()} />
      </Panel>
    </>
  );
}

function HansenTable({ v }: { v: NonNullable<SiteDetail["validation"]> }) {
  const years = Object.keys(v.hansen_loss_ha_by_year);
  return (
    <div>
      <div className="grid grid-cols-3 gap-2 text-center">
        <Mini label="Precision" value={v.precision === null ? "n/a" : fmt.pct(v.precision)} />
        <Mini label="Recall" value={v.recall === null ? "n/a" : fmt.pct(v.recall)} />
        <Mini label="Pixels compared" value={fmt.int(v.pixels_compared)} />
      </div>
      <table className="mt-4 w-full text-[13px]">
        <thead>
          <tr className="text-left text-muted">
            <th className="py-1.5 font-medium">Year</th>
            <th className="py-1.5 text-right font-medium">Hansen loss</th>
            <th className="py-1.5 text-right font-medium">CanopyProof first confirmed</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-[var(--hair)] [font-variant-numeric:tabular-nums]">
          {years.map((y) => (
            <tr key={y}>
              <td className="py-1.5 text-ink">{y}</td>
              <td className="py-1.5 text-right text-ink-2">{fmt.ha(v.hansen_loss_ha_by_year[y])}</td>
              <td className="py-1.5 text-right text-ink-2">{fmt.ha(v.canopyproof_confirmed_ha_by_first_year[y] ?? 0)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Mini({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl bg-paper-2 px-2 py-2.5">
      <p className="t-num text-[19px] text-ink">{value}</p>
      <p className="text-[11.5px] text-muted">{label}</p>
    </div>
  );
}

export function RecordsTable({ rows }: { rows: SiteDetail["series"] }) {
  if (!rows.length) return <Empty title="No measured looks" body="All acquisitions so far were cloudy or outside the monitoring season." />;
  return (
    <div className="-mx-2 overflow-x-auto">
      <table className="w-full min-w-[640px] text-[13px]">
        <thead>
          <tr className="text-left text-muted">
            {["Acquired", "NDVI", "NBR", "Disturbed", "Confirmed loss", "Signal", ""].map((h) => (
              <th key={h} className="px-2 py-2 font-medium">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-[var(--hair)] [font-variant-numeric:tabular-nums]">
          {rows.map((r) => (
            <tr key={r.record_id}>
              <td className="px-2 py-2.5 text-ink">{fmt.date(r.date)}</td>
              <td className="px-2 py-2.5 text-ink-2">{r.ndvi ?? "n/a"}</td>
              <td className="px-2 py-2.5 text-ink-2">{r.nbr ?? "n/a"}</td>
              <td className="px-2 py-2.5 text-ink-2">{fmt.pct(r.disturbed)}</td>
              <td className="px-2 py-2.5 text-ink-2">{fmt.pct(r.loss)}</td>
              <td className="px-2 py-2.5">
                <StatusBadge status={r.status} size="sm" />
              </td>
              <td className="px-2 py-2.5 text-right">
                <button
                  onClick={() => go("verify", r.record_id)}
                  className="rounded-full border border-hair bg-surface px-3 py-1 font-display text-[12.5px] font-semibold text-ink transition hover:-translate-y-px"
                >
                  Verify
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
