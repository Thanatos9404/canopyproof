import { Blocks, Flame, Layers, Link2, ShieldAlert, ShieldCheck } from "lucide-react";
import { useMemo } from "react";
import { Badge, Panel, StatTile } from "../kit";
import { CellMap } from "../components/CellMap";
import { Empty, ErrorState, Skeleton, StatusBadge, fmt } from "../components/ui";
import type { CreditBatch, SiteSummary, Status } from "../lib/api";
import { go, type AppData } from "./ProductApp";

export function batchStatus(cb: CreditBatch): Status {
  return (cb.onChain?.status ?? cb.status) as Status;
}

export function Overview({ data }: { data: AppData }) {
  const sites = data.sites.data?.sites ?? [];
  const integ = data.integrity.data;
  const chain = data.chain.data;
  const batches = integ?.credit_batches ?? [];
  const reversals = batches.filter((b) => batchStatus(b) === "reversal_suspected").length;
  const features = useMemo(
    () => sites.map((s) => ({ id: s.site_id, label: `${s.name}: ${s.latest ? s.latest.status.replace("_", " ") : "no data"}`, geometry: s.geometry, status: (s.latest?.status ?? "insufficient_data") as Status })),
    [sites],
  );
  const evidence = batches
    .flatMap((b) => b.evidence.map((e) => ({ ...e, project: b.project_id })))
    .sort((a, b) => b.observed_at.localeCompare(a.observed_at))
    .slice(0, 7);
  const anchored = chain?.anchoredBatches;

  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatTile icon={<Layers />} label="Credit batches monitored" value={batches.length || "0"} hint="registered in CarbonIntegrityOracle" />
        <StatTile
          icon={<ShieldAlert />}
          label="Reversals suspected"
          value={reversals}
          delta={reversals ? "transfers frozen" : undefined}
          tone={reversals ? "neg" : "neutral"}
          hint="read live from the contract"
        />
        <StatTile icon={<Blocks />} label="Monthly roots anchored" value={anchored ?? "n/a"} hint={`${sites.reduce((n, s) => n + s.records, 0)} signed records`} />
        <StatTile
          icon={<Link2 />}
          label="Chain"
          value={chain?.reachable ? `#${chain.blockNumber}` : "offline"}
          delta={chain?.reachable ? `chain ${chain.chainId}` : undefined}
          tone={chain?.reachable ? "pos" : "neutral"}
          hint={chain?.reachable ? `${chain.network} · latest block` : chain?.reason ?? "connecting"}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-12">
        <Panel className="lg:col-span-7" title="Monitoring cells" description="1 km cells sampling each project. Click one to open its record.">
          {data.sites.loading && !sites.length ? (
            <Skeleton className="h-[360px]" />
          ) : data.sites.error ? (
            <ErrorState message={data.sites.error.message} onRetry={data.sites.retry} />
          ) : (
            <CellMap features={features} onSelect={(id) => go("monitor", id)} label="World map of monitoring cells" height={360} />
          )}
        </Panel>
        <Panel className="lg:col-span-5" title="Credit batch integrity" description="Status the contract would report to a marketplace right now." action={integ?.live ? <Badge tone="pos">Live</Badge> : <Badge>Snapshot</Badge>}>
          {data.integrity.error ? (
            <ErrorState message={data.integrity.error.message} onRetry={data.integrity.retry} />
          ) : !integ ? (
            <div className="space-y-2">
              {[0, 1, 2].map((i) => (
                <Skeleton key={i} className="h-16" />
              ))}
            </div>
          ) : batches.length === 0 ? (
            <Empty title="No credit batches yet" body="Run `python -m canopyproof demo` against a local Hardhat node." />
          ) : (
            <ul className="divide-y divide-[var(--hair)]">
              {batches.map((b) => (
                <li key={b.creditBatchId}>
                  <button onClick={() => go("projects", String(b.creditBatchId))} className="flex w-full items-center justify-between gap-3 py-3 text-left transition-colors hover:bg-[var(--hair)] rounded-xl px-2 -mx-2">
                    <span className="min-w-0">
                      <span className="block font-display text-[14.5px] font-semibold text-ink">
                        #{b.creditBatchId} {b.project_id}
                      </span>
                      <span className="block truncate text-[12.5px] text-muted">
                        vintages {b.vintages[0]}-{b.vintages[1]} · {fmt.int(b.issued_credits)} credits · {b.retirement_check_text}
                      </span>
                    </span>
                    <StatusBadge status={batchStatus(b)} size="sm" />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>

      <div className="grid gap-4 lg:grid-cols-12">
        <Panel className="lg:col-span-7" title="Latest evidence accepted on-chain" description="Submitted by a public watchdog account; each one carries a Merkle proof.">
          {evidence.length === 0 ? (
            <Empty title="No evidence yet" body="Evidence appears after `canopyproof integrity` runs." />
          ) : (
            <ul className="divide-y divide-[var(--hair)]">
              {evidence.map((e) => (
                <li key={e.txHash} className="flex flex-wrap items-center justify-between gap-2 py-2.5 text-[13.5px]">
                  <span className="flex min-w-0 items-center gap-3">
                    <StatusBadge status={e.status} size="sm" />
                    <button onClick={() => go("verify", e.record_id)} className="truncate text-left text-ink underline-offset-4 hover:underline">
                      {e.site_id} · {fmt.date(e.observed_at)}
                    </button>
                  </span>
                  <span className="mono-hash text-muted">{fmt.short(e.txHash)}</span>
                </li>
              ))}
            </ul>
          )}
        </Panel>
        <Panel className="lg:col-span-5" title="Same land, same year, twice?" description="CarbonIntegrityOracle refuses double claims on the land grid.">
          {integ?.relisting_demo ? <DoubleClaim r={integ.relisting_demo} /> : <Skeleton className="h-28" />}
        </Panel>
      </div>
      <FirstReversal sites={sites} />
    </div>
  );
}

function DoubleClaim({ r }: { r: NonNullable<AppData["integrity"]["data"]>["relisting_demo"] & object }) {
  const values = r.overlapping.values ?? [];
  return (
    <div className="space-y-3 text-[13.5px] text-ink-2">
      <div className="flex items-start gap-3 rounded-2xl bg-paper-2 p-3">
        <ShieldAlert size={18} className="mt-0.5 shrink-0 text-[var(--st-reversal)]" strokeWidth={1.7} />
        <p>
          Re-listing <strong className="text-ink">{r.over_site}</strong> for vintages {r.overlapping.vintages[0]}-{r.overlapping.vintages[1]}:{" "}
          <strong className="text-ink">refused</strong> with {r.overlapping.error}
          {values.length > 2 ? ` (year ${values[1]} already claimed by credit batch #${values[2]})` : ""}.
        </p>
      </div>
      <div className="flex items-start gap-3 rounded-2xl bg-paper-2 p-3">
        <ShieldCheck size={18} className="mt-0.5 shrink-0 text-[var(--st-healthy)]" strokeWidth={1.7} />
        <p>
          Vintages {r.later_vintages.vintages[0]}-{r.later_vintages.vintages[1]} on the same land: accepted as credit batch #{r.later_vintages.creditBatchId}.
          The land's reversal history stays on-chain.
        </p>
      </div>
      <p className="text-[12px] text-muted">{r.note}</p>
    </div>
  );
}

function FirstReversal({ sites }: { sites: SiteSummary[] }) {
  const fire = sites.find((s) => s.site_id === "KE-FIRE-01");
  if (!fire?.first_reversal || !fire.chip_first || !fire.chip) return null;
  return (
    <Panel title="Klamath East (ACR273): before and after the 2021 Bootleg Fire" description="Real Sentinel-2 views of the same 1 km cell, with the date the contract flagged it.">
      <div className="grid gap-4 md:grid-cols-[1fr_1fr_1.1fr]">
        {[fire.chip_first, fire.chip].map((c, i) => (
          <figure key={c.url} className="overflow-hidden rounded-[16px] border border-hair">
            <img src={c.url} alt={`Sentinel-2 true colour, ${c.date}`} className="aspect-square w-full object-cover" />
            <figcaption className="flex items-center justify-between px-3 py-2 text-[12.5px] text-muted">
              <span>{i === 0 ? "Baseline" : "Latest"} · {fmt.date(c.date)}</span>
              <span>Copernicus Sentinel-2</span>
            </figcaption>
          </figure>
        ))}
        <div className="flex flex-col justify-between gap-4 rounded-[16px] bg-paper-2 p-5">
          <div>
            <Flame size={20} className="text-[var(--st-reversal)]" strokeWidth={1.7} />
            <p className="mt-3 font-display text-[22px] font-semibold tracking-[-0.02em] text-ink">{fmt.ha(fire.confirmed_loss_ha)} confirmed loss</p>
            <p className="mt-1 text-[13.5px] text-ink-2">
              First flagged {fmt.date(fire.first_reversal.date)} after two clear looks. Hansen GFC agreement: precision{" "}
              {fire.validation?.precision !== null && fire.validation?.precision !== undefined ? fmt.pct(fire.validation.precision) : "n/a"}, recall{" "}
              {fire.validation?.recall !== null && fire.validation?.recall !== undefined ? fmt.pct(fire.validation.recall) : "n/a"}.
            </p>
          </div>
          <button onClick={() => go("verify", fire.first_reversal!.record_id)} className="self-start rounded-full bg-ink px-4 py-2 font-display text-[13.5px] font-semibold text-paper transition hover:-translate-y-px">
            Verify that record
          </button>
        </div>
      </div>
    </Panel>
  );
}
