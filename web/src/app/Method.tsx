import { ExternalLink } from "lucide-react";
import { Panel } from "../kit";
import { KV, fmt } from "../components/ui";
import type { AppData } from "./ProductApp";

const SOURCES = [
  {
    name: "Sentinel-2 L2A, Collection 1",
    who: "ESA Copernicus, via the Earth Search STAC API (Element 84) on the AWS Open Data registry",
    licence: "Copernicus Sentinel data terms: free, with attribution",
    url: "https://element84.com/earth-search/",
  },
  {
    name: "Global Forest Change v1.12 (2000-2024)",
    who: "Hansen et al. 2013, University of Maryland and Google",
    licence: "CC BY 4.0",
    url: "https://storage.googleapis.com/earthenginepartners-hansen/GFC-2024-v1.12/download.html",
  },
  {
    name: "California forest offset project boundaries",
    who: "CarbonPlan, from CARB, ACR and CAR public data (Zenodo 8259842)",
    licence: "CC BY 4.0",
    url: "https://zenodo.org/records/8259842",
  },
  { name: "Basemaps", who: "Esri World Light Gray Canvas (with OpenStreetMap contributors) and Esri World Imagery", licence: "Esri terms of use, attribution shown on the map", url: "https://www.esri.com/en-us/legal/terms/full-master-agreement" },
];

const RULES = [
  ["Valid pixel", "Sentinel-2 scene classes 2, 4, 5, 7 (clouds, shadows, cirrus, water and snow masked)"],
  ["Baseline", "Per 20 m pixel and month, median NDVI and NBR of clear looks in the baseline period"],
  ["Forest", "Baseline NDVI median of at least 0.45"],
  ["Disturbed", "dNBR at least 0.27, or dNDVI at least 0.20 with dNBR at least 0.15"],
  ["Burn-like", "Disturbed by dNBR with NBR at or below 0.10"],
  ["Confirmed loss", "Disturbed in two clear looks at least 5 days apart; cumulative"],
  ["Watch", "At least 2% of forest disturbed, burn-like or confirmed"],
  ["Reversal suspected", "At least 5% of baseline forest confirmed lost (sticky until an auditor resolves it)"],
  ["Advisory AI", "Isolation Forest on the change profile, trained only on earlier years"],
];

const LIMITS = [
  "Satellite change detection is evidence for review, not proof of carbon loss.",
  "Harvest allowed by a project's rules, storms, insects, drought and phenology can all look like change.",
  "A 1 km cell samples a project. It does not measure the whole project or its carbon stock.",
  "Clouds, smoke and haze are masked imperfectly; two-look persistence reduces false alarms but does not remove them.",
  "Nothing here judges additionality, baselines or leakage, the main reasons credits have been found overstated.",
  "The Amazon cell is a representative forest area, not a carbon project. Issued-credit numbers marked illustrative are not registry data.",
  "Local chain and testnets only. The attestor key is a single point of trust; anyone can re-run the pipeline to check it.",
];

export function Method({ data }: { data: AppData }) {
  const chain = data.chain.data;
  return (
    <div className="grid gap-4 lg:grid-cols-12">
      <Panel className="lg:col-span-7" title="Detection rules" description="Fixed, published thresholds. The same numbers are in every record and in the contract.">
        <div className="divide-y divide-[var(--hair)]">
          {RULES.map(([k, v]) => (
            <KV key={k} k={k} v={v} />
          ))}
        </div>
      </Panel>
      <Panel className="lg:col-span-5" title="Contracts" description={chain?.network ? `${chain.network}, chain ${chain.chainId}` : "No deployment found"}>
        <div className="divide-y divide-[var(--hair)]">
          {Object.entries(chain?.contracts ?? {}).map(([name, addr]) => (
            <KV key={name} k={name} v={fmt.short(addr, 8)} mono />
          ))}
          <KV k="Status" v={chain?.reachable ? `live at block ${chain.blockNumber}` : chain?.reason ?? "offline"} />
        </div>
      </Panel>
      <Panel className="lg:col-span-7" title="Limits we state up front">
        <ul className="space-y-2.5 text-[13.5px] leading-relaxed text-ink-2">
          {LIMITS.map((l) => (
            <li key={l} className="rounded-xl bg-paper-2 px-3 py-2.5">
              {l}
            </li>
          ))}
        </ul>
      </Panel>
      <Panel className="lg:col-span-5" title="Open data and licences">
        <ul className="space-y-3 text-[13.5px]">
          {SOURCES.map((s) => (
            <li key={s.name}>
              <a href={s.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-display font-semibold text-ink underline-offset-4 hover:underline">
                {s.name} <ExternalLink size={12} />
              </a>
              <p className="text-ink-2">{s.who}</p>
              <p className="text-[12.5px] text-muted">{s.licence}</p>
            </li>
          ))}
        </ul>
      </Panel>
    </div>
  );
}
