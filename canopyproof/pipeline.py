"""Glue: observations + weather -> analytics -> records -> monthly batches -> per-plot series."""
import json

from .analytics import analyze
from .config import SERIES_DIR
from .records import build_batches, build_document, leaf_fields, leaf_hash, month_key
from .sentinel import load_observations
from .weather import load_weather


def plot_documents(plot):
    obs_doc = load_observations(plot["plot_id"])
    if not obs_doc:
        return []
    observations = obs_doc["observations"]
    results = analyze(observations, load_weather(plot["plot_id"]))
    return [build_document(plot, o, a) for o, a in zip(observations, results)]


def build_all(plots, log=print):
    documents = []
    for plot in plots:
        docs = plot_documents(plot)
        log(f"[{plot['plot_id']}] {len(docs)} records")
        documents.extend(docs)
        write_series(plot, docs)
    summaries = build_batches(documents, log=log)
    return documents, summaries


def write_series(plot, docs):
    """Compact time series for the dashboard (the full records live in the batch files)."""
    rows = []
    for d in docs:
        f = leaf_fields(d)
        a, w = d["analytics"], d["weather"]
        rows.append({
            "record_id": d["record_id"],
            "batch": month_key(d),
            "date": d["observed_at"][:10],
            "t": d["observed_unix"],
            "clear": d["quality"]["status"] == "clear",
            "valid": d["quality"]["valid_fraction"],
            "ndvi": d["indices"]["ndvi"]["mean"] if d["indices"]["ndvi"] else None,
            "ndmi": d["indices"]["ndmi"]["mean"] if d["indices"]["ndmi"] else None,
            "ndvi_expected": a["ndvi_expected"],
            "z": a["ndvi_residual_z"],
            "vci": a["vci_pct"],
            "rain60": w.get("rain_60d_departure_pct") if w.get("status") == "available" else None,
            "rain60_mm": w.get("rain_60d_mm"),
            "rain60_normal_mm": w.get("rain_60d_normal_mm"),
            "iso": a["isolation_forest"]["score"] if a["isolation_forest"] else None,
            "iso_unusual": a["isolation_forest"]["unusual"] if a["isolation_forest"] else None,
            "stress": a["stress_level"],
            "reasons": a["reasons"],
            "leaf": leaf_hash(f),
            "document_hash": f["documentHash"],
        })
    SERIES_DIR.mkdir(parents=True, exist_ok=True)
    out = {k: plot[k] for k in ("plot_id", "name", "district", "state", "regional_context", "boundary_note",
                                "plot_key", "geometry_hash", "area_ha")}
    out["geometry"] = plot["geometry"]
    out["series"] = rows
    (SERIES_DIR / f"{plot['plot_id']}.json").write_text(json.dumps(out, separators=(",", ":")) + "\n", encoding="utf-8")
