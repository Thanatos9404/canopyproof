"""Observation records, their on-chain leaves, and monthly Merkle batches.

A record is a canonical JSON document (see canonical.py). Its keccak256 is the `documentHash`.
The leaf anchored on-chain commits to that hash plus a few fixed-point numbers that a smart
contract can check directly (FieldProofRegistry.Observation):

    leaf = keccak256(bytes.concat(keccak256(abi.encode(Observation))))

Everything in the leaf is derived from the document, so a verifier only needs the document and
its Merkle proof.
"""
import json
from collections import defaultdict
from datetime import datetime, timezone

from eth_abi import encode as abi_encode
from eth_hash.auto import keccak

from . import ANALYTICS_VERSION, BATCH_SCHEMA, PROCESSING_VERSION, RECORD_SCHEMA, __version__
from .analytics import LIMITATIONS
from .canonical import canonical_bytes, document_hash, fixed_int
from .config import BATCHES_DIR, S2_COLLECTION, STAC_API
from .merkle import MerkleTree

OBS_TYPES = ["bytes32", "uint64", "int32", "int32", "uint32", "int32", "int32", "int32", "uint8", "uint8", "bytes32"]
OBS_FIELDS = ["plotKey", "observedAt", "ndviE4", "ndmiE4", "validFractionBps", "vciBps", "ndviZE2",
              "rainDeparture60dBps", "stressLevel", "flags", "documentHash"]
FLAG_QUALITY_CLEAR, FLAG_VCI_AVAILABLE, FLAG_RAIN_AVAILABLE, FLAG_ZSCORE_AVAILABLE, FLAG_CROP_EXPECTED = 1, 2, 4, 8, 16


def build_document(plot, obs, analysis):
    wx = analysis["weather"]
    weather = {"status": wx.get("status"), "provider": "Open-Meteo / ERA5 (ECMWF, Copernicus C3S)"}
    weather.update({k: v for k, v in wx.items() if k != "status"})
    return {
        "schema": RECORD_SCHEMA,
        "record_id": f"{plot['plot_id']}:{obs['item_id']}",
        "plot": {
            "plot_id": plot["plot_id"],
            "plot_key": plot["plot_key"],
            "geometry_hash": plot["geometry_hash"],
            "area_ha": plot["area_ha"],
        },
        "observed_at": obs["observed_at"],
        "observed_unix": obs["observed_unix"],
        "satellite": {
            "mission": "Sentinel-2",
            "platform": obs["platform"],
            "processing_level": "L2A surface reflectance (ESA Collection 1)",
            "stac_api": STAC_API,
            "collection": S2_COLLECTION,
            "item_id": obs["item_id"],
            "item_url": f"{STAC_API}/collections/{S2_COLLECTION}/items/{obs['item_id']}",
            "mgrs_tile": obs["mgrs_tile"],
            "processing_baseline": obs["processing_baseline"],
            "scene_cloud_cover_pct": obs["scene_cloud_cover_pct"],
            "assets": obs["assets"],
            "reflectance_scale_offset": obs["reflectance_scale_offset"],
        },
        "processing": {
            "version": PROCESSING_VERSION,
            "ndvi": "(B08-B04)/(B08+B04), 10 m",
            "ndmi": "(B08-B11)/(B08+B11), 20 m, B08 averaged 2x2",
            "valid_scl_classes": [4, 5],
            "min_valid_fraction": "0.60",
            "grid": obs["grid"],
            "pixel_digest": obs["pixel_digest"],
        },
        "quality": obs["quality"],
        "indices": {"ndvi": obs["ndvi"], "ndmi": obs["ndmi"]},
        "weather": weather,
        "analytics": {k: v for k, v in analysis.items() if k != "weather"},
        "limitations": LIMITATIONS,
        "software": {"name": "fieldproof", "version": __version__, "analytics_version": ANALYTICS_VERSION},
    }


def leaf_fields(doc):
    """Fixed-point values for FieldProofRegistry.Observation, derived only from the document."""
    analytics = doc["analytics"]
    weather = doc["weather"]
    clear = doc["quality"]["status"] == "clear"
    vci = analytics.get("vci_pct")
    z = analytics.get("ndvi_residual_z")
    dep = weather.get("rain_60d_departure_pct") if weather.get("status") == "available" else None
    flags = ((FLAG_QUALITY_CLEAR if clear else 0)
             | (FLAG_VCI_AVAILABLE if vci is not None else 0)
             | (FLAG_RAIN_AVAILABLE if dep is not None else 0)
             | (FLAG_ZSCORE_AVAILABLE if z is not None else 0)
             | (FLAG_CROP_EXPECTED if analytics.get("crop_expected") else 0))
    ndvi = doc["indices"]["ndvi"]
    ndmi = doc["indices"]["ndmi"]
    return {
        "plotKey": doc["plot"]["plot_key"],
        "observedAt": doc["observed_unix"],
        "ndviE4": fixed_int(ndvi["mean"]) if ndvi else 0,
        "ndmiE4": fixed_int(ndmi["mean"]) if ndmi else 0,
        "validFractionBps": fixed_int(doc["quality"]["valid_fraction"]),
        "vciBps": fixed_int(vci, 100) if vci is not None else 0,
        "ndviZE2": fixed_int(z, 100) if z is not None else 0,
        "rainDeparture60dBps": fixed_int(dep, 100) if dep is not None else 0,
        "stressLevel": analytics["stress_code"],
        "flags": flags,
        "documentHash": document_hash(doc),
    }


def _b32(h):
    return bytes.fromhex(h[2:])


def encode_leaf_fields(fields):
    values = [fields[k] for k in OBS_FIELDS]
    values[0] = _b32(values[0])
    values[-1] = _b32(values[-1])
    return abi_encode(OBS_TYPES, values)


def leaf_hash(fields):
    return "0x" + keccak(keccak(encode_leaf_fields(fields))).hex()


def observation_tuple(fields):
    """Argument for web3 contract calls taking an Observation struct."""
    values = [fields[k] for k in OBS_FIELDS]
    values[0] = _b32(values[0])
    values[-1] = _b32(values[-1])
    return tuple(values)


# ----------------------------------------------------------------------------- batches

def month_key(doc):
    return doc["observed_at"][:7]


def build_batches(documents, out_dir=BATCHES_DIR, log=print):
    """Group records by acquisition month, build one Merkle tree per month and write it to disk."""
    groups = defaultdict(list)
    for doc in documents:
        groups[month_key(doc)].append(doc)
    out_dir.mkdir(parents=True, exist_ok=True)
    summaries = []
    for period in sorted(groups):
        docs = sorted(groups[period], key=lambda d: (d["observed_unix"], d["record_id"]))
        entries = []
        for doc in docs:
            fields = leaf_fields(doc)
            entries.append({"record_id": doc["record_id"], "document": doc, "document_hash": fields["documentHash"],
                            "leaf_fields": fields, "leaf": leaf_hash(fields)})
        tree = MerkleTree([e["leaf"] for e in entries])
        for e in entries:
            e["proof"] = tree.proof(e["leaf"])
        manifest = {
            "schema": BATCH_SCHEMA,
            "batch_name": period,
            "period_start": docs[0]["observed_at"],
            "period_end": docs[-1]["observed_at"],
            "period_start_unix": docs[0]["observed_unix"],
            "period_end_unix": docs[-1]["observed_unix"],
            "record_count": len(entries),
            "merkle_root": tree.root,
            "leaf_encoding": "keccak256(bytes.concat(keccak256(abi.encode(FieldProofRegistry.Observation))))",
            "tree": "sorted leaves, sorted-pair keccak256, odd node promoted (OpenZeppelin MerkleProof compatible)",
            "leaves": tree.leaves,
            "records": [{"record_id": e["record_id"], "document_hash": e["document_hash"], "leaf": e["leaf"]}
                        for e in entries],
            "plots": sorted({d["plot"]["plot_id"] for d in docs}),
            "processing_version": PROCESSING_VERSION,
            "analytics_version": ANALYTICS_VERSION,
            "software": f"fieldproof {__version__}",
        }
        manifest_hash = "0x" + keccak(canonical_bytes(manifest)).hex()
        batch_dir = out_dir / period
        batch_dir.mkdir(parents=True, exist_ok=True)
        (batch_dir / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
        (batch_dir / "records.json").write_text(json.dumps(entries, separators=(",", ":")) + "\n", encoding="utf-8")
        summaries.append({"batch": period, "records": len(entries), "merkle_root": tree.root,
                          "manifest_hash": manifest_hash})
    index = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "batches": summaries,
    }
    (out_dir / "index.json").write_text(json.dumps(index, indent=1) + "\n", encoding="utf-8")
    log(f"built {len(summaries)} monthly batches with {sum(s['records'] for s in summaries)} records")
    return summaries


def load_manifest(batch):
    path = BATCHES_DIR / batch / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    return manifest, "0x" + keccak(canonical_bytes(manifest)).hex()


def load_entries(batch):
    return json.loads((BATCHES_DIR / batch / "records.json").read_text(encoding="utf-8"))


def find_entry(record_id):
    """Locate a record by id (`PLOT:ITEM`); returns (batch, entry)."""
    from .config import SERIES_DIR

    series = SERIES_DIR / f"{record_id.split(':', 1)[0]}.json"
    candidates = []
    if series.exists():  # fast path: the per-plot series knows each record's batch
        rows = json.loads(series.read_text(encoding="utf-8"))["series"]
        candidates = [r["batch"] for r in rows if r["record_id"] == record_id]
    if not candidates:
        index = json.loads((BATCHES_DIR / "index.json").read_text(encoding="utf-8"))
        candidates = [b["batch"] for b in index["batches"]]
    for batch in candidates:
        path = BATCHES_DIR / batch / "records.json"
        if not path.exists():
            continue
        for entry in load_entries(batch):
            if entry["record_id"] == record_id:
                return batch, entry
    raise KeyError(f"record {record_id} not found in any batch")
