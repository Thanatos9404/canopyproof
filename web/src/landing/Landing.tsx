import { ArrowRight, Blocks, Eye, FileCheck2, Flame, Gavel, Satellite, ScanSearch, ShieldCheck, TreePine } from "lucide-react";
import {
  Bento,
  BentoCard,
  Button,
  CTASection,
  FeatureSplit,
  FooterCard,
  Hero,
  Marquee,
  Nav,
  ScrollWords,
  SectionHeader,
  SmoothScroll,
  StatTile,
  Wordmark,
} from "../kit";
import { Legend, TimeChart } from "../components/TimeChart";
import { Skeleton, StatusBadge, fmt } from "../components/ui";
import { useApi, type ChainStatus, type Integrity, type RecordPayload, type SiteSummary, type Status } from "../lib/api";

const GITHUB = "https://github.com/Thanatos9404/canopyproof";
const BOOTLEG_START = "2021-07-06"; // Bootleg Fire ignition date (US Forest Service / InciWeb)

export function Landing() {
  const sites = useApi<{ sites: SiteSummary[] }>("/api/sites");
  const integrity = useApi<Integrity>("/api/integrity");
  const chain = useApi<ChainStatus>("/api/chain");
  const list = sites.data?.sites ?? [];
  const fire = list.find((s) => s.site_id === "KE-FIRE-01");
  const record = useApi<RecordPayload>(fire?.first_reversal ? `/api/records/${encodeURIComponent(fire.first_reversal.record_id)}` : null);

  return (
    <SmoothScroll>
      <Nav
        brand={<Wordmark name="CANOPY" suffix=".proof" />}
        links={[
          { label: "Problem", href: "#problem" },
          { label: "How it works", href: "#how" },
          { label: "Evidence", href: "#evidence" },
          { label: "Limits", href: "#limits" },
        ]}
        cta={{ label: "Open the monitor", href: "#app" }}
      />
      <Hero
        eyebrow={
          <>
            <TreePine size={14} strokeWidth={1.8} className="text-accent" /> Carbon markets track · IEEE ClimateChain 2026
          </>
        }
        title="Is the forest behind your credit *still* standing?"
        subtitle="CanopyProof watches forest carbon projects with free Sentinel-2 imagery, anchors every observation on-chain, and lets anyone check it."
        primary={{ label: "Open the monitor", href: "#app" }}
        secondary={{ label: "Verify a record", href: "#app/verify" }}
        product={<ProductPreview sites={list} integrity={integrity.data} loading={sites.loading} error={sites.error?.message} />}
      />

      <section className="section-y !pb-0">
        <div className="container-x">
          <p className="t-eyebrow mb-6 text-center">Built with open data and open tools</p>
          <Marquee
            items={[
              "Copernicus Sentinel-2",
              "Earth Search STAC",
              "Hansen Global Forest Change",
              "CarbonPlan boundaries",
              "Solidity 0.8.28",
              "OpenZeppelin 5",
              "Hardhat",
              "web3.py",
              "rasterio + GDAL",
              "scikit-learn",
              "ethers.js",
              "React + Motion",
            ]}
          />
        </div>
      </section>

      <section id="problem" className="section-y">
        <div className="container-x">
          <ScrollWords text="Of 89 million credits expected from 18 forest projects, researchers linked only *6%* to real emission cuts." />
          <p className="mx-auto mt-8 max-w-[64ch] text-center text-[13.5px] leading-relaxed text-muted">
            West et al., <em>Science</em> 381:873 (2023), on voluntary REDD+ projects; Verra disputes the method. The same year the Guardian, Die Zeit and
            SourceMaterial reported that most of Verra's rainforest credits were likely "phantom". Separately, the 2021 Bootleg Fire burned through Oregon's
            Klamath East project, and at least 1.14 million credits had to be cancelled from California's buffer pool (CarbonPlan). The evidence behind ratings is
            mostly closed; buyers cannot check for themselves.
          </p>
        </div>
      </section>

      <section id="how" className="section-y !pt-0">
        <div className="container-x space-y-28">
          <FeatureSplit
            eyebrow="Observe"
            title="Every forest pixel, every clear look."
            body="Each monitoring cell is read straight from Sentinel-2 Cloud-Optimized GeoTIFFs. NDVI and the Normalized Burn Ratio are compared, pixel by pixel, with the project's own pre-issuance baseline. Smoke is ignored; loss must show in two clear looks."
            bullets={[
              { icon: <Satellite size={16} />, label: "Sentinel-2, 20 m" },
              { icon: <Flame size={16} />, label: "Burn-aware" },
              { icon: <Eye size={16} />, label: "Two-look rule" },
              { icon: <ScanSearch size={16} />, label: "Hansen cross-check" },
            ]}
            visual={<BeforeAfter site={fire} />}
          />
          <FeatureSplit
            flip
            eyebrow="Anchor"
            title="Evidence on-chain, not just a rating."
            body="Every look becomes a canonical JSON record. Its keccak256, plus the numbers a contract needs, form a Merkle leaf; the attestor signs each leaf and anchors one root per month. Change one digit and the proof fails."
            action={
              <Button href="#app/verify" icon={<ArrowRight size={16} />}>
                Verify it yourself
              </Button>
            }
            visual={<RecordCard rec={record.data} />}
          />
          <FeatureSplit
            eyebrow="Enforce"
            title="Contracts that react to the forest."
            body="CarbonIntegrityOracle turns anchored evidence into a public status per credit batch. A suspected reversal flags the buffer pool and freezes transfers in a marketplace that asks first. The same land cannot be claimed twice for one vintage."
            visual={<IntegrityCard integrity={integrity.data} />}
          />
        </div>
      </section>

      <section id="pipeline" className="section-y bg-paper">
        <div className="container-x">
          <SectionHeader eyebrow="How it works" title="Five steps, all reproducible" lead="Open data in, signed evidence out, and a contract anyone can query." />
          <Bento className="mt-14">
            <BentoCard
              span={7}
              icon={<Satellite size={18} />}
              title="Observe"
              body="Range-requests only the pixels of each 1 km cell from public Sentinel-2 COGs: no API keys, no paid imagery."
              visual={<FireSpark site={fire} />}
            />
            <BentoCard span={5} icon={<ScanSearch size={18} />} title="Detect" body="Seasonal per-pixel baselines, dNBR and dNDVI rules, two-look persistence, and an Isolation Forest trained only on earlier years as an advisory flag." />
            <BentoCard span={4} icon={<Blocks size={18} />} title="Commit" body="Canonical JSON, keccak256, sorted-pair Merkle trees, EIP-191 record signatures and EIP-712 signed batch roots." />
            <BentoCard span={4} icon={<Gavel size={18} />} title="Enforce" body="Per-batch thresholds, sticky reversals, auditor resolutions with public reasons, buffer-pool flags and double-claim checks." />
            <BentoCard span={4} icon={<ShieldCheck size={18} />} title="Verify" body="Anyone can rebuild a leaf, walk the proof, ask the contract, and re-read the same Sentinel-2 pixels to reproduce the digest." />
          </Bento>
        </div>
      </section>

      <section id="evidence" className="section-y !pt-0">
        <div className="container-x">
          <SectionHeader eyebrow="Results" title="Numbers we measured" align="left" lead="From the demo run on four real 1 km cells, 2019 to October 2026." />
          <Results sites={list} chain={chain.data} />
        </div>
      </section>

      <section id="limits" className="section-y !pt-0">
        <div className="container-x">
          <div className="paper-card grid gap-8 p-8 md:grid-cols-[1fr_1.4fr] md:p-10">
            <div>
              <p className="t-eyebrow mb-3">Honest limits</p>
              <h2 className="t-h2 !text-[clamp(28px,3vw,40px)]">Evidence, not a verdict.</h2>
            </div>
            <ul className="space-y-3 text-[15px] leading-relaxed text-ink-2">
              <li>Satellite change is evidence for review, not proof of carbon loss: permitted harvest, storms and phenology can look alike.</li>
              <li>It checks permanence. It does not judge additionality, baselines or leakage, the main reasons credits were found overstated.</li>
              <li>A 1 km cell samples a project. The Amazon cell is a representative forest area, not a carbon project.</li>
              <li>Prototype on a local chain and public testnets only. No funds, no real credits.</li>
            </ul>
          </div>
        </div>
      </section>

      <CTASection
        title={
          <>
            Check the forest, <span className="t-serif">not</span> the brochure.
          </>
        }
        lead="Open the monitor, pick any record, and verify it against the contract in your browser."
        action={
          <Button href="#app" size="lg" icon={<ArrowRight size={17} />}>
            Open the monitor
          </Button>
        }
      />
      <FooterCard
        brand={<Wordmark name="CANOPY" suffix=".proof" />}
        blurb="Open, satellite-verified integrity monitoring for forest carbon credits. Built for the IEEE ClimateChain Global Hackathon 2026, Carbon Markets track."
        columns={[
          {
            title: "Product",
            links: [
              { label: "Monitor", href: "#app" },
              { label: "Verify a record", href: "#app/verify" },
              { label: "Method and limits", href: "#app/method" },
            ],
          },
          {
            title: "Open source",
            links: [
              { label: "GitHub (MIT)", href: GITHUB },
              { label: "Hansen GFC", href: "https://glad.earthengine.app/view/global-forest-change" },
              { label: "CarbonPlan data", href: "https://zenodo.org/records/8259842" },
            ],
          },
        ]}
        legal={
          <span>
            Contains modified Copernicus Sentinel data (2019-2026). Hansen/UMD/Google GFC and CarbonPlan boundaries under CC BY 4.0. Map tiles: Esri, HERE, Garmin and OpenStreetMap
            contributors. Fonts are OFL. Code MIT, by Yashvardhan Thanvi.
          </span>
        }
      />
    </SmoothScroll>
  );
}

/* ---------------------------------------------------------------- real mini UIs fed by the API */

function ProductPreview({ sites, integrity, loading, error }: { sites: SiteSummary[]; integrity: Integrity | null; loading: boolean; error?: string }) {
  const fire = sites.find((s) => s.site_id === "KE-FIRE-01");
  return (
    <div className="grid text-left md:grid-cols-[230px_1fr]">
      <div className="hidden border-r border-hair bg-paper-2/60 p-4 md:block">
        <Wordmark name="CANOPY" suffix=".proof" />
        <p className="t-eyebrow mt-6 !text-[10.5px]">Credit batches</p>
        <ul className="mt-2 space-y-1.5">
          {(integrity?.credit_batches ?? []).map((b) => (
            <li key={b.creditBatchId} className="flex items-center justify-between rounded-lg bg-surface px-2.5 py-1.5 text-[12.5px]">
              <span className="font-display font-semibold text-ink">{b.project_id}</span>
              <StatusBadge status={(b.onChain?.status ?? b.status) as Status} size="sm" />
            </li>
          ))}
          {!integrity && [0, 1, 2].map((i) => <Skeleton key={i} className="h-8" />)}
        </ul>
      </div>
      <div className="p-4 md:p-5">
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="font-display text-[15px] font-semibold text-ink">{fire ? fire.name : "Klamath East"}</p>
            <p className="text-[12px] text-muted">ACR273 · 1 km monitoring cell · Sentinel-2 since 2019</p>
          </div>
          {fire?.latest && <StatusBadge status={fire.latest.status} />}
        </div>
        {error ? (
          <p className="mt-6 rounded-xl bg-paper-2 p-4 text-[13px] text-ink-2">Live data appears when the API runs: python -m canopyproof serve</p>
        ) : loading || !fire ? (
          <Skeleton className="mt-4 h-[260px]" />
        ) : (
          <div className="mt-4 grid gap-3 sm:grid-cols-[1fr_1fr_1.2fr]">
            {[fire.chip_first, fire.chip].map(
              (c, i) =>
                c && (
                  <figure key={c.url} className="overflow-hidden rounded-xl border border-hair">
                    <img src={c.url} alt={`Sentinel-2 view ${c.date}`} className="aspect-square w-full object-cover" />
                    <figcaption className="px-2 py-1.5 text-[11.5px] text-muted">
                      {i === 0 ? "Baseline" : "Now"} · {fmt.date(c.date)}
                    </figcaption>
                  </figure>
                ),
            )}
            <div className="rounded-xl border border-hair p-3">
              <p className="text-[11.5px] text-muted">Confirmed loss</p>
              <p className="t-num text-[26px] text-ink">{fmt.ha(fire.confirmed_loss_ha)}</p>
              <p className="mt-2 text-[11.5px] text-muted">First flagged</p>
              <p className="font-display text-[14px] font-semibold text-ink">{fire.first_reversal ? fmt.date(fire.first_reversal.date) : "n/a"}</p>
              <p className="mt-2 text-[11.5px] text-muted">Hansen GFC recall</p>
              <p className="font-display text-[14px] font-semibold text-ink">{fire.validation?.recall != null ? fmt.pct(fire.validation.recall) : "n/a"}</p>
            </div>
          </div>
        )}
        {fire && !error && <FireSpark site={fire} compact />}
      </div>
    </div>
  );
}

function FireSpark({ site, compact }: { site?: SiteSummary; compact?: boolean }) {
  const detail = useApi<{ series: SiteSummary["latest"][] }>(site ? `/api/sites/${site.site_id}` : null);
  const rows = (detail.data?.series ?? []).filter((r): r is NonNullable<typeof r> => !!r && r.status !== "insufficient_data");
  if (!rows.length) return <Skeleton className={compact ? "mt-3 h-[120px]" : "h-[150px]"} />;
  return (
    <div className={compact ? "mt-3" : ""}>
      {!compact && <Legend items={[{ label: "NDVI", colour: "var(--series-ndvi)" }, { label: "NBR", colour: "var(--series-nbr)" }]} />}
      <TimeChart
        data={rows.map((r) => ({ t: r.t, date: r.date, status: r.status, values: { ndvi: r.ndvi ? Number(r.ndvi) : null, nbr: r.nbr ? Number(r.nbr) : null } }))}
        series={[
          { key: "ndvi", label: "NDVI", colour: "var(--series-ndvi)", fill: true },
          { key: "nbr", label: "NBR", colour: "var(--series-nbr)" },
        ]}
        yDomain={[-0.4, 1]}
        yTicks={[-0.4, 0, 0.5, 1]}
        events={site?.first_reversal ? [{ t: site.first_reversal.t, label: "flagged" }] : []}
        height={compact ? 130 : 160}
        showStatusMarkers={false}
        ariaLabel="NDVI and NBR before and after the 2021 Bootleg Fire"
      />
    </div>
  );
}

function BeforeAfter({ site }: { site?: SiteSummary }) {
  if (!site?.chip || !site.chip_first) return <Skeleton className="h-[320px]" />;
  return (
    <div className="surface p-4 text-left">
      <div className="flex items-center justify-between">
        <p className="font-display text-[15px] font-semibold text-ink">{site.name}</p>
        {site.latest && <StatusBadge status={site.latest.status} size="sm" />}
      </div>
      <div className="mt-3 grid grid-cols-2 gap-2">
        {[site.chip_first, site.chip].map((c, i) => (
          <figure key={c.url} className="overflow-hidden rounded-xl">
            <img src={c.url} alt={`Sentinel-2 true colour ${c.date}`} className="aspect-square w-full object-cover" />
            <figcaption className="mt-1 text-[12px] text-muted">
              {i === 0 ? "Baseline" : "Latest"} · {fmt.date(c.date)}
            </figcaption>
          </figure>
        ))}
      </div>
      <div className="mt-3 flex items-center justify-between rounded-xl bg-paper-2 px-3 py-2.5 text-[13px]">
        <span className="text-ink-2">Confirmed loss since baseline</span>
        <strong className="font-display text-ink">{fmt.ha(site.confirmed_loss_ha)}</strong>
      </div>
    </div>
  );
}

function RecordCard({ rec }: { rec: RecordPayload | null }) {
  if (!rec) return <Skeleton className="h-[300px]" />;
  const rows: [string, string][] = [
    ["Record", rec.record_id],
    ["Document hash", fmt.short(rec.document_hash, 10)],
    ["Merkle leaf", fmt.short(rec.leaf, 10)],
    ["Batch root", fmt.short(rec.merkle_root, 10)],
    ["Anchored", rec.anchor ? `batch #${rec.anchor.batchId} · block ${rec.anchor.blockNumber}` : "not anchored here"],
  ];
  return (
    <div className="surface p-5 text-left">
      <div className="flex items-center justify-between">
        <p className="font-display text-[15px] font-semibold text-ink">Signed observation record</p>
        <StatusBadge status={rec.document.change.status} size="sm" />
      </div>
      <div className="mt-4 space-y-2">
        {rows.map(([k, v]) => (
          <div key={k} className="flex items-center justify-between gap-3 rounded-xl bg-paper-2 px-3 py-2.5 text-[13px]">
            <span className="text-muted">{k}</span>
            <span className="mono-hash truncate text-right text-ink">{v}</span>
          </div>
        ))}
      </div>
      <p className="mt-3 flex items-center gap-1.5 text-[12px] text-muted">
        <FileCheck2 size={14} /> keccak256 · sorted-pair Merkle · EIP-191 + EIP-712 signatures
      </p>
    </div>
  );
}

function IntegrityCard({ integrity }: { integrity: Integrity | null }) {
  if (!integrity?.credit_batches.length) return <Skeleton className="h-[300px]" />;
  const r = integrity.relisting_demo;
  const blocked = integrity.ledger_demo.find((l) => !l.retired);
  return (
    <div className="surface p-5 text-left">
      <p className="font-display text-[15px] font-semibold text-ink">CarbonIntegrityOracle</p>
      <ul className="mt-3 space-y-2">
        {integrity.credit_batches.map((b) => (
          <li key={b.creditBatchId} className="flex items-center justify-between rounded-xl bg-paper-2 px-3 py-2.5 text-[13px]">
            <span className="text-ink">
              #{b.creditBatchId} {b.project_id} <span className="text-muted">· {b.vintages.join("-")}</span>
            </span>
            <StatusBadge status={(b.onChain?.status ?? b.status) as Status} size="sm" />
          </li>
        ))}
      </ul>
      {r && (
        <p className="mt-3 rounded-xl border border-hair px-3 py-2.5 text-[12.5px] text-ink-2">
          Re-list {r.over_site} for {r.overlapping.vintages.join("-")}: <strong className="text-ink">{r.overlapping.error ?? "accepted"}</strong>
        </p>
      )}
      {blocked && (
        <p className="mt-2 rounded-xl border border-hair px-3 py-2.5 text-[12.5px] text-ink-2">
          Retire credits of batch #{blocked.creditBatchId}: <strong className="text-ink">IntegrityHold({blocked.check})</strong>
        </p>
      )}
    </div>
  );
}

function Results({ sites, chain }: { sites: SiteSummary[]; chain: ChainStatus | null }) {
  const fire = sites.find((s) => s.site_id === "KE-FIRE-01");
  const records = sites.reduce((n, s) => n + s.records, 0);
  const lagDays = fire?.first_reversal ? Math.round((Date.parse(fire.first_reversal.date) - Date.parse(BOOTLEG_START)) / 86_400_000) : null;
  const intact = sites.filter((s) => s.site_id !== "KE-FIRE-01" && s.site_id !== "AMZ-FRONTIER-01");
  const maxFalse = intact.length ? Math.max(...intact.map((s) => s.confirmed_loss_ha)) : null;
  if (!sites.length) {
    return (
      <div className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-32" />
        ))}
      </div>
    );
  }
  return (
    <div className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <StatTile icon={<Flame />} label="Bootleg Fire flagged" value={lagDays !== null ? `${lagDays} days` : "n/a"} hint="from ignition (6 Jul 2021) to the second clear burned look" />
      <StatTile
        icon={<ScanSearch />}
        label="Hansen GFC recall, fire cell"
        value={fire?.validation?.recall != null ? fmt.pct(fire.validation.recall) : "n/a"}
        delta={fire?.validation?.precision != null ? `precision ${fmt.pct(fire.validation.precision)}` : undefined}
        tone="pos"
        hint={`pixel by pixel, ${fire?.validation?.pixels_compared ?? 0} Hansen pixels`}
      />
      <StatTile icon={<TreePine />} label="Loss confirmed in intact cells" value={maxFalse !== null ? `<= ${fmt.ha(maxFalse)}` : "n/a"} hint="per 100 ha cell where Hansen shows no loss: below the 2% watch line" />
      <StatTile icon={<Blocks />} label="Records anchored" value={fmt.int(records)} hint={chain?.reachable ? `${chain.anchoredBatches} monthly Merkle roots, local chain` : "monthly Merkle roots"} />
    </div>
  );
}
