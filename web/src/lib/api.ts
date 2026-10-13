import { useCallback, useEffect, useState } from "react";

export type Status = "healthy" | "watch" | "reversal_suspected" | "insufficient_data" | "unmonitored";

export type SeriesRow = {
  record_id: string;
  batch: string;
  date: string;
  t: number;
  clear: boolean;
  valid: string;
  ndvi: string | null;
  nbr: string | null;
  d_ndvi: string | null;
  d_nbr: string | null;
  disturbed: string | null;
  burn: string | null;
  loss: string | null;
  loss_ha: string | null;
  iso: string | null;
  iso_flag: boolean | null;
  baseline: boolean;
  status: Status;
  reasons: string[];
  leaf: string;
  document_hash: string;
};

export type Polygon = { type: "Polygon"; coordinates: number[][][] };
export type Bounds = [[number, number], [number, number]];
export type Chip = { url: string; date: string; bounds: Bounds; item_id: string; attribution: string };

export type Validation = {
  pixels_compared: number;
  hansen_loss_pixels: number;
  canopyproof_confirmed_pixels: number;
  agreeing_pixels: number;
  precision: number | null;
  recall: number | null;
  hansen_loss_ha_by_year: Record<string, number>;
  canopyproof_confirmed_ha_by_first_year: Record<string, number>;
  source: string;
  method: string;
};

export type ProjectInfo = {
  project_id: string;
  name: string;
  registry: string;
  type: string;
  country: string;
  region: string;
  real_project: boolean;
  documented_events: { date: string; text: string; source: string }[];
  credit_batch: {
    label: string;
    vintage_start: number;
    vintage_end: number;
    issued_credits: number;
    issued_note: string;
    baseline_start: string;
    baseline_end: string;
    monitoring_months: number[];
    monitoring_months_note: string;
  };
};

export type SiteSummary = {
  site_id: string;
  project_id: string;
  name: string;
  role: string;
  selection: string;
  boundary_source: string;
  site_key: string;
  project_key: string;
  geometry_hash: string;
  area_ha: string;
  geometry: Polygon;
  centroid: [number, number];
  project: ProjectInfo;
  forest_ha: number;
  baseline_scenes: number;
  confirmed_loss_ha: number;
  changemap: { url: string; bounds: Bounds; confirmed_pixels: number; forest_pixels: number };
  records: number;
  measured_records: number;
  first_date: string;
  last_date: string;
  status_counts: Record<string, number>;
  latest: SeriesRow | null;
  first_reversal: SeriesRow | null;
  chip: Chip | null;
  chip_first: Chip | null;
  validation: Validation | null;
};

export type Note = { text: string; disclaimer: string; generated_by: string; ai_generated: boolean; source_record: string };
export type SiteDetail = SiteSummary & { series: SeriesRow[]; note: Note | null };

export type Evidence = {
  site_id: string;
  record_id: string;
  observed_at: string;
  status: Status;
  cumulative_loss_bps: number;
  registryBatchId: number;
  txHash: string;
  buffer_pool_review_flagged: boolean;
};

export type CreditBatch = {
  project_id: string;
  creditBatchId: number;
  txHash: string;
  vintages: [number, number];
  issued_credits: number;
  sites: string[];
  land_cells: number;
  gasUsed: number;
  status: Status;
  stale: boolean;
  indicative_affected_bps: number;
  indicative_affected_credits: number;
  buffer_review_flagged: boolean;
  retirement_check: number;
  retirement_check_text: string;
  evidence: Evidence[];
  onChain?: {
    status: Status;
    stale: boolean;
    retirementCheck: number;
    indicativeAffectedCredits: number;
    indicativeAffectedBps: number;
    oldestObservation: number;
  };
};

export type Integrity = {
  network: string | null;
  live: boolean;
  created_at?: string;
  thresholds?: { watch_bps: number; reversal_bps: number; max_evidence_age_days: number };
  credit_batches: CreditBatch[];
  relisting_demo: {
    project_id: string;
    note: string;
    over_site: string;
    overlapping: { vintages: [number, number]; accepted: boolean; error?: string; values?: (string | number)[] };
    later_vintages: { vintages: [number, number]; accepted: boolean; creditBatchId: number; txHash: string };
  } | null;
  ledger_demo: { creditBatchId: number; project_id: string; retired: boolean; check?: number; reason?: string; txHash?: string }[];
};

export type ChainStatus = {
  network: string | null;
  chainId?: number;
  reachable: boolean;
  reason?: string;
  blockNumber?: number;
  anchoredBatches?: number;
  creditBatches?: number;
  contracts?: Record<string, string>;
  explorer?: string | null;
};

export type AppState = {
  app: string;
  version: string;
  network: string | null;
  networks: string[];
  batch_count: number;
  record_count: number;
  sites: Pick<SiteSummary, "site_id" | "project_id" | "name" | "role" | "area_ha" | "geometry" | "centroid" | "latest" | "confirmed_loss_ha" | "forest_ha" | "chip">[];
};

export type RecordPayload = {
  record_id: string;
  batch: string;
  document: Record<string, unknown> & {
    observed_at: string;
    observed_unix: number;
    site: { site_id: string; site_key: string; project_key: string };
    quality: { status: string; valid_fraction: string };
    indices: { ndvi: { mean: string } | null; nbr: { mean: string } | null };
    change: {
      status: Status;
      status_code: number;
      cumulative_loss_fraction: string | null;
      in_baseline_period: boolean;
      isolation_forest: { advisory_flag: boolean } | null;
      fractions_bps: { disturbed: number; persistent: number; burn: number; cumulative: number };
    };
    satellite: { item_url: string; item_id: string };
    processing: { pixel_digest: string };
  };
  document_hash: string;
  leaf_fields: Record<string, string | number>;
  leaf: string;
  proof: string[];
  merkle_root: string;
  manifest_hash: string;
  network: string | null;
  anchor: { batchId: number; txHash: string; blockNumber: number; attestor: string; registry: string; mode: string; recordSignature: string | null } | null;
  explorer: string | null;
};

export type VerifyStep = { step: string; ok: boolean | null; detail: string };
export type VerifyReport = { record_id: string; verified: boolean; steps: VerifyStep[] };

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function getJSON<T>(path: string): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, { headers: { Accept: "application/json" } });
  } catch {
    throw new ApiError(0, "The CanopyProof API is not reachable. Start it with `python -m canopyproof serve`.");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

/** Fetch with loading / error / retry state. Keeps the previous data while refetching. */
export function useApi<T>(path: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(Boolean(path));
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!path) return;
    let alive = true;
    setLoading(true);
    getJSON<T>(path)
      .then((d) => {
        if (alive) {
          setData(d);
          setError(null);
        }
      })
      .catch((e: ApiError) => alive && setError(e))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [path, tick]);
  const retry = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, loading, retry };
}
