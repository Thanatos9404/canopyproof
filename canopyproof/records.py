"""Observation records, their on-chain leaves, and monthly Merkle batches.

A record is a canonical JSON document (see canonical.py). Its keccak256 is the `documentHash`.
The leaf anchored on-chain commits to that hash plus the fixed-point numbers the integrity contract
checks directly (CanopyProofRegistry.Observation):

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
from .canonical import canonical_bytes, document_hash, fixed_int
from .config import BATCHES_DIR, S2_COLLECTION, STAC_API
from .forest import LIMITATIONS
from .merkle import MerkleTree
from .sentinel import METHOD

OBS_TYPES = ["bytes32", "bytes32", "uint64", "int32", "int32", "uint32", "uint32", "uint32", "uint32", "uint32",
             "uint8", "uint8", "bytes32"]
OBS_FIELDS = ["siteKey", "projectKey", "observedAt", "ndviE4", "nbrE4", "validFractionBps", "disturbedBps",
              "persistentBps", "burnBps", "cumulativeLossBps", "status", "flags", "documentHash"]
FLAG_CLEAR, FLAG_BASELINE_READY, FLAG_ML_UNUSUAL, FLAG_BASELINE_PERIOD = 1, 2, 4, 8


def build_document(site, obs, analysis):
    return {
        "schema": RECORD_SCHEMA,
        "record_id": f"{site['site_id']}:{obs['item_id']}",
        "site": {
            "site_id": site["site_id"],
            "site_key": site["site_key"],
            "geometry_hash": site["geometry_hash"],
            "area_ha": site["area_ha"],
            "project_id": site["project_id"],
            "project_key": site["project_key"],
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
        "processing": {**METHOD, "grid": obs["grid"], "pixel_digest": obs["pixel_digest"]},
        "quality": obs["quality"],
        "indices": {"ndvi": obs["ndvi"], "nbr": obs["nbr"]},
        "change": analysis,
        "limitations": LIMITATIONS,
        "software": {"name": "canopyproof", "version": __version__, "analytics_version": ANALYTICS_VERSION},
    }


def leaf_fields(doc):
    """Fixed-point values for CanopyProofRegistry.Observation, derived only from the document."""
    ch = doc["change"]
    measured = ch["cumulative_loss_fraction"] is not None
    flags = ((FLAG_CLEAR if doc["quality"]["status"] == "clear" else 0)
             | (FLAG_BASELINE_READY if measured else 0)
             | (FLAG_ML_UNUSUAL if (ch["isolation_forest"] or {}).get("advisory_flag") else 0)
             | (FLAG_BASELINE_PERIOD if ch["in_baseline_period"] else 0))
    ndvi, nbr = doc["indices"]["ndvi"], doc["indices"]["nbr"]
    bps = ch["fractions_bps"]
    return {
        "siteKey": doc["site"]["site_key"],
        "projectKey": doc["site"]["project_key"],
        "observedAt": doc["observed_unix"],
        "ndviE4": fixed_int(ndvi["mean"]) if ndvi else 0,
        "nbrE4": fixed_int(nbr["mean"]) if nbr else 0,
        "validFractionBps": fixed_int(doc["quality"]["valid_fraction"]),
        "disturbedBps": bps["disturbed"] if measured else 0,
        "persistentBps": bps["persistent"] if measured else 0,
        "burnBps": bps["burn"] if measured else 0,
        "cumulativeLossBps": bps["cumulative"] if measured else 0,
        "status": ch["status_code"],
        "flags": flags,
        "documentHash": document_hash(doc),
    }


def _b32(h):
    return bytes.fromhex(h[2:])


def _values(fields):
    return [_b32(fields[k]) if t == "bytes32" else fields[k] for k, t in zip(OBS_FIELDS, OBS_TYPES)]


def encode_leaf_fields(fields):
    return abi_encode(OBS_TYPES, _values(fields))


def leaf_hash(fields):
    return "0x" + keccak(keccak(encode_leaf_fields(fields))).hex()


def observation_tuple(fields):
    """Argument for web3 contract calls taking an Observation struct."""
    return tuple(_values(fields))


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
            "leaf_encoding": "keccak256(bytes.concat(keccak256(abi.encode(CanopyProofRegistry.Observation))))",
            "tree": "sorted leaves, sorted-pair keccak256, odd node promoted (OpenZeppelin MerkleProof compatible)",
            "leaves": tree.leaves,
            "records": [{"record_id": e["record_id"], "document_hash": e["document_hash"], "leaf": e["leaf"]}
                        for e in entries],
            "sites": sorted({d["site"]["site_id"] for d in docs}),
            "processing_version": PROCESSING_VERSION,
            "analytics_version": ANALYTICS_VERSION,
            "software": f"canopyproof {__version__}",
        }
        manifest_hash = "0x" + keccak(canonical_bytes(manifest)).hex()
        batch_dir = out_dir / period
        batch_dir.mkdir(parents=True, exist_ok=True)
        (batch_dir / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8", newline="
")
        (batch_dir / "records.json").write_text(json.dumps(entries, separators=(",", ":")) + "\n", encoding="utf-8", newline="
")
        summaries.append({"batch": period, "records": len(entries), "merkle_root": tree.root,
                          "manifest_hash": manifest_hash})
    index = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "batches": summaries,
    }
    (out_dir / "index.json").write_text(json.dumps(index, indent=1) + "\n", encoding="utf-8", newline="
")
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
    if series.exists():  # fast path: the per-site series knows each record's batch
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
