"""Glue: cached Sentinel-2 rasters -> change analytics -> records -> monthly batches -> series and maps."""
import json

from .config import DATA, SERIES_DIR
from .forest import analyze_site
from .hansen import change_map
from .records import build_batches, build_document, leaf_fields, leaf_hash, month_key
from .sentinel import load_observations, load_rasters

CHANGEMAPS_DIR = DATA / "changemaps"


def site_documents(site, project):
    """Records for one monitoring cell, plus the analysis summary (forest mask, first-confirmation map)."""
    obs_doc = load_observations(site["site_id"])
    rasters = load_rasters(site["site_id"])
    if not obs_doc or not rasters:
        return [], None, None
    observations = [o for o in obs_doc["observations"] if o["item_id"] in rasters]
    cb = project["credit_batch"]
    results, summary = analyze_site(observations, rasters, cb["baseline_start"], cb["baseline_end"],
                                    cb.get("monitoring_months"))
    docs = [build_document(site, o, a) for o, a in zip(observations, results)]
    return docs, summary, {**obs_doc, "observations": observations}


def build_all(sites, projects, log=print):
    by_project = {p["project_id"]: p for p in projects}
    documents, summaries = [], {}
    for site in sites:
        docs, summary, obs_doc = site_documents(site, by_project[site["project_id"]])
        if not docs:
            log(f"[{site['site_id']}] no observations yet (run `canopyproof fetch`)")
            continue
        documents.extend(docs)
        summaries[site["site_id"]] = (summary, obs_doc)
        cmap = change_map(site, obs_doc, summary, CHANGEMAPS_DIR)
        write_series(site, by_project[site["project_id"]], docs, summary, cmap)
        latest = next((d for d in reversed(docs) if d["change"]["status"] != "insufficient_data"), None)
        log(f"[{site['site_id']}] {len(docs)} records, forest {summary['forest_ha']} ha, confirmed loss "
            f"{summary['confirmed_ha']} ha, latest status {latest['change']['status'] if latest else 'n/a'}")
    batches = build_batches(documents, log=log)
    return documents, batches, summaries


def write_series(site, project, docs, summary, cmap):
    """Compact time series for the web app (the full records live in the batch files)."""
    rows = []
    for d in docs:
        f = leaf_fields(d)
        c = d["change"]
        rows.append({
            "record_id": d["record_id"],
            "batch": month_key(d),
            "date": d["observed_at"][:10],
            "t": d["observed_unix"],
            "clear": d["quality"]["status"] == "clear",
            "valid": d["quality"]["valid_fraction"],
            "ndvi": d["indices"]["ndvi"]["mean"] if d["indices"]["ndvi"] else None,
            "nbr": d["indices"]["nbr"]["mean"] if d["indices"]["nbr"] else None,
            "d_ndvi": c["mean_d_ndvi"],
            "d_nbr": c["mean_d_nbr"],
            "disturbed": c["disturbed_fraction"],
            "burn": c["burn_like_fraction"],
            "loss": c["cumulative_loss_fraction"],
            "loss_ha": c["confirmed_loss_ha"],
            "iso": c["isolation_forest"]["score"] if c["isolation_forest"] else None,
            "iso_flag": c["isolation_forest"]["advisory_flag"] if c["isolation_forest"] else None,
            "baseline": c["in_baseline_period"],
            "status": c["status"],
            "reasons": c["reasons"],
            "leaf": leaf_hash(f),
            "document_hash": f["documentHash"],
        })
    SERIES_DIR.mkdir(parents=True, exist_ok=True)
    out = {k: site[k] for k in ("site_id", "project_id", "name", "role", "selection", "boundary_source",
                                "site_key", "project_key", "geometry_hash", "area_ha", "geometry", "centroid")}
    out["project"] = {k: project[k] for k in ("project_id", "name", "registry", "type", "country", "region",
                                              "real_project", "documented_events", "credit_batch")}
    out["forest_ha"] = summary["forest_ha"]
    out["baseline_scenes"] = summary["baseline_scenes"]
    out["confirmed_loss_ha"] = summary["confirmed_ha"]
    out["changemap"] = {**cmap, "url": f"/data/changemaps/{site['site_id']}.png"}
    out["series"] = rows
    (SERIES_DIR / f"{site['site_id']}.json").write_text(json.dumps(out, separators=(",", ":")) + "\n", encoding="utf-8")
