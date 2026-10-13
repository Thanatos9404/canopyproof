import { motion } from "motion/react";
import { Check, CircleDashed, Info, Loader2, Satellite, ShieldCheck, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Badge, Button, EASE, Panel, Segmented, cn } from "../kit";
import { Empty, ErrorState, KV, Skeleton, StatusBadge, fmt } from "../components/ui";
import { getJSON, useApi, type RecordPayload, type SiteDetail, type VerifyReport, type VerifyStep } from "../lib/api";
import { verifyInBrowser } from "../lib/verify";
import { go, type AppData } from "./ProductApp";

type Run = { steps: VerifyStep[]; done: boolean; ok: boolean | null };

export function Verify({ data, recordId }: { data: AppData; recordId?: string }) {
  const sites = data.sites.data?.sites ?? [];
  const fallbackSite = recordId?.split(":")[0] ?? "KE-FIRE-01";
  const [siteId, setSiteId] = useState(fallbackSite);
  useEffect(() => setSiteId(fallbackSite), [fallbackSite]);
  const detail = useApi<SiteDetail>(`/api/sites/${encodeURIComponent(siteId)}`);
  const measured = useMemo(() => (detail.data?.series ?? []).filter((r) => r.status !== "insufficient_data"), [detail.data]);
  const chosen = recordId ?? detail.data?.first_reversal?.record_id ?? measured[measured.length - 1]?.record_id;
  const rec = useApi<RecordPayload>(chosen ? `/api/records/${encodeURIComponent(chosen)}` : null);

  const [tamper, setTamper] = useState(false);
  const [run, setRun] = useState<Run | null>(null);
  const [recompute, setRecompute] = useState<{ loading: boolean; report?: VerifyReport; error?: string } | null>(null);
  useEffect(() => {
    setRun(null);
    setRecompute(null);
  }, [chosen, tamper]);

  const start = async () => {
    if (!rec.data) return;
    const steps: VerifyStep[] = [];
    setRun({ steps: [], done: false, ok: null });
    const ok = await verifyInBrowser(rec.data, tamper ? { field: "loss", value: "0.0000" } : null, (s) => {
      steps.push(s);
      setRun({ steps: [...steps], done: false, ok: null });
    });
    setRun({ steps: [...steps], done: true, ok });
  };

  const startRecompute = async () => {
    if (!chosen) return;
    setRecompute({ loading: true });
    try {
      const report = await getJSON<VerifyReport>(`/api/verify/${encodeURIComponent(chosen)}?recompute=1`);
      setRecompute({ loading: false, report });
    } catch (e) {
      setRecompute({ loading: false, error: (e as Error).message });
    }
  };

  const recomputeStep = recompute?.report?.steps.find((s) => s.step.startsWith("Recomputed"));

  return (
    <div className="grid gap-4 lg:grid-cols-12">
      <div className="space-y-4 lg:col-span-7">
        <Panel
          title="Pick a record"
          description="Every Sentinel-2 look is a signed, content-hashed record inside a monthly Merkle batch."
          action={
            sites.length ? (
              <Segmented
                id="verify-site"
                value={siteId}
                onChange={(v) => {
                  const s = sites.find((x) => x.site_id === v);
                  const target = s?.first_reversal?.record_id ?? s?.latest?.record_id;
                  if (target) go("verify", target);
                }}
                options={sites.slice(0, 4).map((s) => ({ value: s.site_id, label: s.site_id.replace(/-0\d$/, "") }))}
              />
            ) : undefined
          }
        >
          {detail.error ? (
            <ErrorState message={detail.error.message} onRetry={detail.retry} />
          ) : !detail.data ? (
            <Skeleton className="h-11" />
          ) : (
            <label className="block">
              <span className="sr-only">Record</span>
              <select
                value={chosen}
                onChange={(e) => go("verify", e.target.value)}
                className="h-11 w-full rounded-xl border border-hair bg-surface px-3 text-[14px] text-ink outline-none focus-visible:border-[var(--accent)]"
              >
                {measured
                  .slice()
                  .reverse()
                  .map((r) => (
                    <option key={r.record_id} value={r.record_id}>
                      {r.date} · {r.status.replace("_", " ")} · confirmed loss {fmt.pct(r.loss)}
                    </option>
                  ))}
              </select>
            </label>
          )}
          {rec.data && (
            <div className="mt-4 divide-y divide-[var(--hair)]">
              <KV k="Record" v={rec.data.record_id} mono />
              <KV k="Acquired" v={fmt.date(rec.data.document.observed_at)} />
              <KV k="Integrity signal" v={<StatusBadge status={rec.data.document.change.status} size="sm" />} />
              <KV k="Document hash" v={rec.data.document_hash} mono />
              <KV k={`Merkle root, batch ${rec.data.batch}`} v={rec.data.merkle_root} mono />
              <KV
                k="Anchored"
                v={
                  rec.data.anchor ? (
                    <span>
                      batch #{rec.data.anchor.batchId} · block {rec.data.anchor.blockNumber} · <span className="mono-hash">{fmt.short(rec.data.anchor.txHash)}</span>
                    </span>
                  ) : (
                    "not anchored on this network"
                  )
                }
              />
            </div>
          )}
        </Panel>

        <Panel title="Raw record" description="The canonical JSON whose keccak256 is committed in the leaf. Nothing else is trusted.">
          {rec.data ? (
            <pre className="max-h-[360px] overflow-auto rounded-xl bg-paper-2 p-4 text-[11.5px] leading-relaxed text-ink-2">
              {JSON.stringify(rec.data.document, null, 2)}
            </pre>
          ) : rec.error ? (
            <ErrorState message={rec.error.message} onRetry={rec.retry} />
          ) : (
            <Skeleton className="h-40" />
          )}
        </Panel>
      </div>

      <div className="space-y-4 lg:col-span-5">
        <Panel
          title="Verify in your browser"
          description="ethers.js recomputes the hashes and asks the contract through a read-only RPC proxy."
          action={run?.done ? <Badge tone={run.ok ? "pos" : "neg"}>{run.ok ? "Verified" : "Not verified"}</Badge> : undefined}
        >
          <label className="flex cursor-pointer items-center gap-3 rounded-xl bg-paper-2 px-3 py-2.5 text-[13.5px] text-ink-2">
            <input type="checkbox" checked={tamper} onChange={(e) => setTamper(e.target.checked)} className="size-4 accent-[var(--accent)]" />
            Simulate tampering: set confirmed loss to 0% in my local copy
          </label>
          <div className="mt-4 flex flex-wrap gap-2">
            <Button onClick={start} disabled={!rec.data || (run !== null && !run.done)} icon={<ShieldCheck size={16} />}>
              {run && !run.done ? "Checking" : "Run the checks"}
            </Button>
            <Button variant="glass" onClick={startRecompute} disabled={!chosen || recompute?.loading} icon={<Satellite size={16} />}>
              {recompute?.loading ? "Re-reading Sentinel-2" : "Recompute from Sentinel-2"}
            </Button>
          </div>
          {!run ? (
            <div className="mt-5">
              <Empty title="Nothing checked yet" body="Run the checks to rebuild the leaf, walk the Merkle proof and query the contract." />
            </div>
          ) : (
            <ol className="mt-5 space-y-2">
              {run.steps.map((s, i) => (
                <motion.li
                  key={`${s.step}-${i}`}
                  initial={{ opacity: 0, y: 8, filter: "blur(4px)" }}
                  animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
                  transition={{ duration: 0.4, ease: EASE }}
                  className="flex gap-3 rounded-xl border border-hair bg-surface px-3 py-2.5"
                >
                  <StepIcon ok={s.ok} />
                  <span className="min-w-0">
                    <span className="block text-[13.5px] font-medium text-ink">{s.step}</span>
                    <span className="mono-hash block text-muted">{s.detail}</span>
                  </span>
                </motion.li>
              ))}
              {!run.done && (
                <li className="flex items-center gap-2 px-3 py-2 text-[13px] text-muted">
                  <Loader2 size={15} className="animate-spin" /> working
                </li>
              )}
            </ol>
          )}
          {recompute && !recompute.loading && (
            <div className={cn("mt-4 rounded-xl p-3 text-[13px]", recomputeStep?.ok ? "bg-[color-mix(in_oklab,var(--st-healthy)_10%,transparent)]" : "bg-paper-2")}>
              {recompute.error ? (
                <span className="text-ink-2">{recompute.error}</span>
              ) : (
                <span className="flex gap-2 text-ink-2">
                  <StepIcon ok={recomputeStep?.ok ?? null} />
                  <span>
                    <strong className="text-ink">Recomputed from the original COGs.</strong> {recomputeStep?.detail}
                  </span>
                </span>
              )}
            </div>
          )}
        </Panel>
        <Panel title="What a pass means">
          <ul className="space-y-2 text-[13.5px] text-ink-2">
            <li className="flex gap-2">
              <Info size={15} className="mt-0.5 shrink-0 text-muted" /> The record is exactly what the attestor anchored, in a batch that has not been revoked.
            </li>
            <li className="flex gap-2">
              <Info size={15} className="mt-0.5 shrink-0 text-muted" /> Recompute re-reads the same Sentinel-2 pixels and must reproduce the pixel digest.
            </li>
            <li className="flex gap-2">
              <Info size={15} className="mt-0.5 shrink-0 text-muted" /> It does not prove carbon was lost: it proves the evidence is genuine and reproducible.
            </li>
          </ul>
        </Panel>
      </div>
    </div>
  );
}

function StepIcon({ ok }: { ok: boolean | null }) {
  if (ok === null) return <CircleDashed size={18} className="mt-0.5 shrink-0 text-muted" strokeWidth={1.8} />;
  return ok ? (
    <span className="mt-0.5 grid size-[18px] shrink-0 place-items-center rounded-full bg-[var(--st-healthy)] text-white">
      <Check size={12} strokeWidth={3} />
    </span>
  ) : (
    <span className="mt-0.5 grid size-[18px] shrink-0 place-items-center rounded-full bg-[var(--st-reversal)] text-white">
      <X size={12} strokeWidth={3} />
    </span>
  );
}
