"""JSON API used by the web app. Pure functions returning JSON-serialisable dicts.

Read-only by design: the API can read records, proofs and contract state and run the same
verification as the CLI, but it can never sign or send a transaction.
"""
import json
from functools import lru_cache

from . import ANALYTICS_VERSION, PROCESSING_VERSION, __version__
from .config import (ALLOWED_CHAINS, BATCHES_DIR, CHAIN_DIR, CHIPS_DIR, INTEGRITY_DIR, NOTES_DIR, PROJECTS_FILE,
                     SERIES_DIR, VALIDATION_DIR)

NETWORKS = list(ALLOWED_CHAINS.values())
STATUS = ["unmonitored", "healthy", "watch", "reversal_suspected"]


class NotFound(KeyError):
    pass


def _read(path):
    if not path.exists():
        raise NotFound(str(path.name))
    return json.loads(path.read_text(encoding="utf-8"))


def _maybe(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def deployed_networks():
    return [n for n in NETWORKS if (CHAIN_DIR / f"{n}.json").exists()]


def default_network():
    nets = deployed_networks()
    return "localhost" if "localhost" in nets else (nets[0] if nets else None)


# ----------------------------------------------------------------------------- sites

def _chip(name):
    meta = _maybe(CHIPS_DIR / f"{name}.json")
    return None if not meta else {**meta, "url": f"/data/chips/{name}.png"}


def _site_summary(doc):
    rows = doc["series"]
    measured = [r for r in rows if r["status"] != "insufficient_data"]
    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    first_rev = next((r for r in measured if r["status"] == "reversal_suspected"), None)
    return {
        **{k: doc[k] for k in ("site_id", "project_id", "name", "role", "selection", "boundary_source", "site_key",
                               "project_key", "geometry_hash", "area_ha", "geometry", "centroid", "project",
                               "forest_ha", "baseline_scenes", "confirmed_loss_ha", "changemap")},
        "records": len(rows),
        "measured_records": len(measured),
        "first_date": rows[0]["date"] if rows else None,
        "last_date": rows[-1]["date"] if rows else None,
        "status_counts": counts,
        "latest": measured[-1] if measured else None,
        "first_reversal": first_rev,
        "chip": _chip(doc["site_id"]),
        "chip_first": _chip(doc["site_id"] + ".first"),
        "validation": _maybe(VALIDATION_DIR / f"{doc['site_id']}.json"),
    }


def sites():
    return {"sites": [_site_summary(json.loads(p.read_text(encoding="utf-8"))) for p in sorted(SERIES_DIR.glob("*.json"))]}


def site_detail(site_id):
    doc = _read(SERIES_DIR / f"{site_id}.json")
    return {**_site_summary(doc), "series": doc["series"], "note": _maybe(NOTES_DIR / f"{site_id}.json")}


def projects():
    data = _read(PROJECTS_FILE)
    summaries = sites()["sites"]
    out = []
    for p in data["projects"]:
        own = [s for s in summaries if s["project_id"] == p["project_id"]]
        worst = max((s["latest"]["status"] for s in own if s["latest"]),
                    key=lambda st: ["insufficient_data", "healthy", "watch", "reversal_suspected"].index(st), default=None)
        out.append({**p, "sites": [s["site_id"] for s in own], "offchain_status": worst})
    return {"projects": out, "relisting_demo": data.get("relisting_demo")}


# ----------------------------------------------------------------------------- batches & records

def batches(network=None):
    network = network or default_network()
    index = _read(BATCHES_DIR / "index.json")
    out = []
    for b in index["batches"]:
        att = _maybe(BATCHES_DIR / b["batch"] / f"attestation.{network}.json") if network else None
        out.append({**b, "anchor": None if not att else {k: att[k] for k in (
            "batchId", "txHash", "blockNumber", "attestor", "mode", "anchoredAt", "gasUsed")}})
    return {"network": network, "generated_at": index["generated_at"], "batches": out}


def batch_detail(name, network=None):
    from .records import load_manifest

    network = network or default_network()
    try:
        manifest, manifest_hash = load_manifest(name)
    except FileNotFoundError as exc:
        raise NotFound(name) from exc
    att = _maybe(BATCHES_DIR / name / f"attestation.{network}.json") if network else None
    if att:
        att = {k: v for k, v in att.items() if k != "recordSignatures"}
    return {"network": network, "manifest": manifest, "manifest_hash": manifest_hash, "attestation": att}


def record(record_id, network=None):
    """Everything a client needs to verify one record independently (document, leaf, proof, root, signature)."""
    from .records import OBS_FIELDS, OBS_TYPES, find_entry, load_manifest

    network = network or default_network()
    try:
        batch, entry = find_entry(record_id)
    except (KeyError, FileNotFoundError) as exc:
        raise NotFound(record_id) from exc
    manifest, manifest_hash = load_manifest(batch)
    att = _maybe(BATCHES_DIR / batch / f"attestation.{network}.json") if network else None
    deployment = _maybe(CHAIN_DIR / f"{network}.json") if network else None
    return {
        "record_id": record_id,
        "batch": batch,
        "document": entry["document"],
        "document_hash": entry["document_hash"],
        "leaf_fields": entry["leaf_fields"],
        "leaf": entry["leaf"],
        "proof": entry["proof"],
        "merkle_root": manifest["merkle_root"],
        "manifest_hash": manifest_hash,
        "network": network,
        "anchor": None if not att else {
            "batchId": att["batchId"], "txHash": att["txHash"], "blockNumber": att["blockNumber"],
            "attestor": att["attestor"], "registry": att["registry"], "mode": att["mode"],
            "recordSignature": att["recordSignatures"].get(entry["leaf"]),
        },
        "explorer": deployment.get("explorer") if deployment else None,
        "encoding": {
            "canonical_json": "sorted keys, no whitespace, ASCII, no floats (decimals are strings)",
            "document_hash": "keccak256(canonical JSON bytes)",
            "leaf": "keccak256(bytes.concat(keccak256(abi.encode(Observation))))",
            "observation_abi": [f"{t} {n}" for t, n in zip(OBS_TYPES, OBS_FIELDS)],
            "tree": "sorted-pair keccak256 (OpenZeppelin MerkleProof)",
            "record_signature": "EIP-191 personal_sign over the 32-byte leaf",
        },
    }


def verify(record_id, network=None, tamper=None, offline=False, recompute=False):
    from .verify import verify_record

    try:
        report = verify_record(record_id, network=None if offline else (network or default_network()),
                               tamper=tamper, recompute=recompute)
    except (KeyError, FileNotFoundError) as exc:
        raise NotFound(record_id) from exc
    return report.to_dict()


# ----------------------------------------------------------------------------- chain

@lru_cache(maxsize=8)
def _w3(network):
    from web3 import Web3

    from .chain import rpc_url_for

    return Web3(Web3.HTTPProvider(rpc_url_for(network), request_kwargs={"timeout": 10}))


def _contract(d, name, w3):
    return w3.eth.contract(address=d["contracts"][name]["address"], abi=d["contracts"][name]["abi"])


def contracts_info(network=None):
    network = network or default_network()
    if not network:
        raise NotFound("no deployment")
    d = _read(CHAIN_DIR / f"{network}.json")
    return {k: d.get(k) for k in ("network", "chainId", "explorer", "deployedAt", "deployBlock", "roles", "contracts")}


def chain_status(network=None):
    network = network or default_network()
    if not network:
        return {"network": None, "reachable": False, "reason": "no deployment file in data/chain"}
    d = _read(CHAIN_DIR / f"{network}.json")
    out = {"network": network, "chainId": d["chainId"], "explorer": d.get("explorer"),
           "contracts": {k: v["address"] for k, v in d["contracts"].items()}, "roles": d.get("roles"),
           "reachable": False}
    try:
        w3 = _w3(network)
        if w3.eth.chain_id != d["chainId"]:
            out["reason"] = "RPC chain id does not match the deployment"
            return out
        registry, oracle = _contract(d, "CanopyProofRegistry", w3), _contract(d, "CarbonIntegrityOracle", w3)
        if not w3.eth.get_code(registry.address):
            out["reason"] = "no contract code at the registry address (local chain restarted?)"
            return out
        out.update(reachable=True, blockNumber=w3.eth.block_number,
                   anchoredBatches=registry.functions.batchCount().call(),
                   creditBatches=oracle.functions.creditBatchCount().call())
    except Exception as exc:
        out["reason"] = f"RPC unavailable ({type(exc).__name__})"
    return out


def integrity(network=None):
    """Credit batches with their live on-chain integrity status (falls back to the last saved run)."""
    network = network or default_network()
    doc = _maybe(INTEGRITY_DIR / f"{network}.json") if network else None
    if not doc:
        return {"network": network, "live": False, "credit_batches": [], "relisting_demo": None, "ledger_demo": []}
    live = False
    try:
        d = _read(CHAIN_DIR / f"{network}.json")
        oracle = _contract(d, "CarbonIntegrityOracle", _w3(network))
        for cb in doc["credit_batches"]:
            st = oracle.functions.integrityStatus(cb["creditBatchId"]).call()
            cb["onChain"] = {"status": STATUS[st[0]], "oldestObservation": st[1], "stale": st[2],
                             "indicativeAffectedBps": st[3], "sitesMonitored": st[4],
                             "retirementCheck": oracle.functions.retirementCheck(cb["creditBatchId"]).call(),
                             "indicativeAffectedCredits": oracle.functions.indicativeAffectedCredits(cb["creditBatchId"]).call()}
        live = True
    except Exception:
        pass
    return {**doc, "live": live}


def state():
    summaries = sites()["sites"]
    index = _maybe(BATCHES_DIR / "index.json") or {"batches": []}
    return {
        "app": "CanopyProof",
        "version": __version__,
        "processing_version": PROCESSING_VERSION,
        "analytics_version": ANALYTICS_VERSION,
        "network": default_network(),
        "networks": deployed_networks(),
        "sites": [{k: s[k] for k in ("site_id", "project_id", "name", "role", "area_ha", "geometry", "centroid",
                                     "latest", "confirmed_loss_ha", "forest_ha", "chip")} for s in summaries],
        "batch_count": len(index["batches"]),
        "record_count": sum(b["records"] for b in index["batches"]),
        "sources": [
            {"name": "Sentinel-2 L2A Collection 1", "provider": "ESA Copernicus via Earth Search (Element 84, AWS Open Data)",
             "licence": "Copernicus Sentinel data terms (free, with attribution)"},
            {"name": "Hansen Global Forest Change v1.12", "provider": "University of Maryland / Google",
             "licence": "CC BY 4.0"},
            {"name": "California offset project boundaries", "provider": "CarbonPlan (from CARB, ACR, CAR)",
             "licence": "CC BY 4.0"},
        ],
    }


ENDPOINTS = {
    "GET /api/health": "liveness",
    "GET /api/state": "app summary, sites, networks",
    "GET /api/sites": "monitoring cells with latest status, validation and images",
    "GET /api/sites/{site_id}": "cell detail with the full time series and integrity note",
    "GET /api/projects": "projects, their credit batches and off-chain status",
    "GET /api/integrity?network=": "credit batches with live on-chain integrity status",
    "GET /api/batches?network=": "monthly batches and their anchors",
    "GET /api/batches/{YYYY-MM}?network=": "batch manifest and attestation",
    "GET /api/records/{record_id}?network=": "record, leaf, proof, root and signature for client-side checks",
    "GET /api/verify/{record_id}?network=&tamper=loss=0&offline=1&recompute=1": "server-side verification report",
    "GET /api/chain?network=": "chain reachability, block, batch counts",
    "GET /api/contracts?network=": "contract addresses and ABIs",
    "POST /rpc/{network}": "read-only JSON-RPC proxy (eth_call, eth_getLogs, ...)",
}
