import { ExternalLink, Flag, Lock, ShoppingBag } from "lucide-react";
import { Badge, Panel } from "../kit";
import { Empty, ErrorState, KV, Skeleton, StatusBadge, fmt } from "../components/ui";
import { useApi, type ProjectInfo } from "../lib/api";
import { batchStatus } from "./Overview";
import { go, type AppData } from "./ProductApp";

const CHECK_TEXT: Record<number, string> = {
  0: "OK to retire",
  1: "Not yet monitored",
  2: "Evidence is stale",
  3: "Watch: review before retiring",
  4: "Reversal suspected: transfers frozen",
};

export function Projects({ data }: { data: AppData }) {
  const projects = useApi<{ projects: (ProjectInfo & { sites: string[] })[] }>("/api/projects");
  const integ = data.integrity.data;
  if (data.integrity.error) return <ErrorState message={data.integrity.error.message} onRetry={data.integrity.retry} />;
  if (!integ || !projects.data) {
    return (
      <div className="grid gap-4 lg:grid-cols-2">
        <Skeleton className="h-[420px]" />
        <Skeleton className="h-[420px]" />
      </div>
    );
  }
  if (!integ.credit_batches.length) {
    return <Empty title="No credit batches registered" body="Run `python -m canopyproof demo` with a local Hardhat node, then refresh." />;
  }
  const byId = new Map(projects.data.projects.map((p) => [p.project_id, p]));
  return (
    <div className="space-y-4">
      <div className="grid gap-4 xl:grid-cols-2">
        {integ.credit_batches.map((b) => {
          const p = byId.get(b.project_id);
          const status = batchStatus(b);
          const check = b.onChain?.retirementCheck ?? b.retirement_check;
          const affected = b.onChain?.indicativeAffectedCredits ?? b.indicative_affected_credits;
          return (
            <Panel
              key={b.creditBatchId}
              title={
                <span className="flex flex-wrap items-center gap-2">
                  Credit batch #{b.creditBatchId} · {p?.name ?? b.project_id}
                  {p && !p.real_project && <Badge>Representative</Badge>}
                </span>
              }
              description={p ? `${p.type} · ${p.region}, ${p.country}` : undefined}
              action={<StatusBadge status={status} />}
            >
              <div className="divide-y divide-[var(--hair)]">
                <KV k="Registry" v={p?.registry ?? "n/a"} />
                <KV k="Vintages" v={`${b.vintages[0]}-${b.vintages[1]}`} />
                <KV k="Issued credits" v={<span title={p?.credit_batch.issued_note}>{fmt.int(b.issued_credits)}</span>} />
                <KV k="Monitoring cells" v={b.sites.join(", ")} />
                <KV k="Land-grid cells claimed" v={`${b.land_cells} (about 1 km each)`} />
                <KV
                  k="Indicative affected credits"
                  v={
                    <span>
                      {fmt.int(affected)} <span className="text-muted">({fmt.bps(b.onChain?.indicativeAffectedBps ?? b.indicative_affected_bps)} mean confirmed loss)</span>
                    </span>
                  }
                />
                <KV
                  k="Buffer-pool review"
                  v={b.buffer_review_flagged ? <span className="inline-flex items-center gap-1 text-[var(--st-reversal)]"><Flag size={14} /> flagged on-chain</span> : "not flagged"}
                />
                <KV k="Marketplace check" v={<span className="inline-flex items-center gap-1">{check === 4 && <Lock size={13} />}{CHECK_TEXT[check]}</span>} />
              </div>
              {p?.documented_events.length ? (
                <div className="mt-5">
                  <p className="t-eyebrow mb-2">Documented independently</p>
                  <ul className="space-y-2">
                    {p.documented_events.map((e) => (
                      <li key={e.text} className="rounded-xl bg-paper-2 px-3 py-2.5 text-[13px] text-ink-2">
                        <span className="font-semibold text-ink">{e.date}</span> {e.text}{" "}
                        <a href={e.source} target="_blank" rel="noreferrer" className="inline-flex items-center gap-0.5 text-ink underline-offset-4 hover:underline">
                          source <ExternalLink size={12} />
                        </a>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
              <div className="mt-5">
                <p className="t-eyebrow mb-2">Evidence timeline on-chain</p>
                <ol className="relative space-y-2 border-l border-hair pl-4">
                  {b.evidence.map((e) => (
                    <li key={e.txHash} className="relative text-[13px]">
                      <span className="status-dot absolute -left-[21px] top-1.5" style={{ background: `var(--st-${e.status === "reversal_suspected" ? "reversal" : e.status === "watch" ? "watch" : "healthy"})` }} />
                      <button onClick={() => go("verify", e.record_id)} className="text-left hover:underline underline-offset-4">
                        <span className="text-ink">{fmt.date(e.observed_at)}</span> <span className="text-muted">{e.site_id}</span>
                      </button>{" "}
                      <StatusBadge status={e.status} size="sm" />
                      {e.buffer_pool_review_flagged && <span className="ml-1 text-[12px] text-[var(--st-reversal)]">buffer review flagged</span>}
                    </li>
                  ))}
                </ol>
              </div>
            </Panel>
          );
        })}
      </div>
      <Panel title="Example marketplace: GuardedCreditLedger" description="A demo ledger that asks the oracle before every transfer and retirement. No real credits.">
        <ul className="grid gap-3 md:grid-cols-3">
          {integ.ledger_demo.map((l) => (
            <li key={l.creditBatchId} className="rounded-2xl bg-paper-2 p-4 text-[13.5px]">
              <p className="flex items-center gap-2 font-display font-semibold text-ink">
                <ShoppingBag size={16} strokeWidth={1.7} /> Retire 10 credits of #{l.creditBatchId}
              </p>
              <p className="mt-2 text-ink-2">
                {l.retired ? (
                  <span className="text-[var(--st-healthy)]">Retired. Status was healthy and fresh.</span>
                ) : (
                  <span className="text-[var(--st-reversal)]">Blocked: IntegrityHold({l.check}), {CHECK_TEXT[l.check ?? 0]?.toLowerCase()}.</span>
                )}
              </p>
            </li>
          ))}
        </ul>
      </Panel>
    </div>
  );
}
