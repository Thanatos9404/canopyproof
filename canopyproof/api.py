"""JSON API used by the web app. Pure functions returning JSON-serialisable dicts.

Everything here is read-only: the API can read records, proofs and contract state, and run the same
verification as the CLI, but it can never sign or send a transaction.
"""
import json
from functools import lru_cache

from . import ANALYTICS_VERSION, PROCESSING_VERSION, __version__
from .config import ALLOWED_CHAINS, BATCHES_DIR, CHAIN_DIR, CHIPS_DIR, EXPLANATIONS_DIR, PROGRAMS_DIR, SERIES_DIR

NETWORKS = list(ALLOWED_CHAINS.values())


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


# ----------------------------------------------------------------------------- plots & series

def _series(plot_id):
    return _read(SERIES_DIR / f"{plot_id}.json")


def _plot_summary(doc):
    rows = doc["series"]
    clear = [r for r in rows if r["clear"]]
    latest = clear[-1] if clear else None
    counts = {}
    for r in rows:
        counts[r["stress"]] = counts.get(r["stress"], 0) + 1
    chip = _maybe(CHIPS_DIR / f"{doc['plot_id']}.json")
    return {
        **{k: doc[k] for k in ("plot_id", "name", "district", "state", "regional_context", "boundary_note",
                               "plot_key", "geometry_hash", "area_ha", "geometry")},
        "records": len(rows),
        "clear_records": len(clear),
        "first_date": rows[0]["date"] if rows else None,
        "last_date": rows[-1]["date"] if rows else None,
        "stress_counts": counts,
        "latest_clear": latest,
        "chip": None if not chip else {**chip, "url": f"/data/chips/{doc['plot_id']}.png"},
    }


def plots():
    out = []
    for path in sorted(SERIES_DIR.glob("*.json")):
        out.append(_plot_summary(json.loads(path.read_text(encoding="utf-8"))))
    return {"plots": out}


def plot_detail(plot_id):
    doc = _series(plot_id)
    return {**_plot_summary(doc), "series": doc["series"], "notes": {
        lang: _maybe(EXPLANATIONS_DIR / f"{plot_id}.{lang}.json") for lang in ("en", "hi")}}


def note(plot_id, lang="en"):
    return _read(EXPLANATIONS_DIR / f"{plot_id}.{lang}.json")


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
    network = network or default_network()
    from .records import load_manifest

    manifest, manifest_hash = load_manifest(name)
    att = _maybe(BATCHES_DIR / name / f"attestation.{network}.json") if network else None
    if att:
        att = {k: v for k, v in att.items() if k != "recordSignatures"}
    return {"network": network, "manifest": manifest, "manifest_hash": manifest_hash, "attestation": att}


def record(record_id, network=None):
    """Everything a client needs to verify one record independently (document, leaf, proof, root, signature)."""
    from .records import find_entry, load_manifest

    network = network or default_network()
    try:
        batch, entry = find_entry(record_id)
    except KeyError as exc:
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
            "observation_abi": ["bytes32 plotKey", "uint64 observedAt", "int32 ndviE4", "int32 ndmiE4",
                                "uint32 validFractionBps", "int32 vciBps", "int32 ndviZE2",
                                "int32 rainDeparture60dBps", "uint8 stressLevel", "uint8 flags",
                                "bytes32 documentHash"],
            "tree": "sorted-pair keccak256 (OpenZeppelin MerkleProof)",
            "record_signature": "EIP-191 personal_sign over the 32-byte leaf",
        },
    }


def verify(record_id, network=None, tamper=None, offline=False, recompute=False):
    from .verify import verify_record

    try:
        report = verify_record(record_id, network=None if offline else (network or default_network()),
                               tamper=tamper, recompute=recompute)
    except KeyError as exc:
        raise NotFound(record_id) from exc
    return report.to_dict()


# ----------------------------------------------------------------------------- chain

@lru_cache(maxsize=8)
def _w3(network):
    from web3 import Web3

    from .chain import rpc_url_for

    return Web3(Web3.HTTPProvider(rpc_url_for(network), request_kwargs={"timeout": 10}))


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
           "registry": d["contracts"]["FieldProofRegistry"]["address"],
           "trigger": d["contracts"]["ParametricDroughtTrigger"]["address"],
           "roles": d.get("roles"), "reachable": False}
    try:
        w3 = _w3(network)
        if w3.eth.chain_id != d["chainId"]:
            out["reason"] = "RPC chain id does not match the deployment"
            return out
        registry = w3.eth.contract(address=out["registry"], abi=d["contracts"]["FieldProofRegistry"]["abi"])
        trigger = w3.eth.contract(address=out["trigger"], abi=d["contracts"]["ParametricDroughtTrigger"]["abi"])
        if not w3.eth.get_code(out["registry"]):
            out["reason"] = "no contract code at the registry address (local chain restarted?)"
            return out
        out.update(reachable=True, blockNumber=w3.eth.block_number,
                   batchCount=registry.functions.batchCount().call(),
                   programCount=trigger.functions.programCount().call())
    except Exception as exc:
        out["reason"] = f"RPC unavailable ({type(exc).__name__})"
    return out


def programs(network=None, plot_id=None):
    network = network or default_network()
    doc = _maybe(PROGRAMS_DIR / f"{network}.json") if network else None
    if not doc:
        return {"network": network, "programs": [], "rules": None, "live": False}
    items = [p for p in doc["programs"] if not plot_id or p["plot_id"] == plot_id]
    live = False
    try:
        d = _read(CHAIN_DIR / f"{network}.json")
        w3 = _w3(network)
        trigger = w3.eth.contract(address=d["contracts"]["ParametricDroughtTrigger"]["address"],
                                  abi=d["contracts"]["ParametricDroughtTrigger"]["abi"])
        for p in items:
            on_chain = trigger.functions.getProgram(p["programId"]).call()
            p["onChain"] = {"qualifyingCount": on_chain[8], "triggered": on_chain[9], "triggeredAt": on_chain[10]}
        live = True
    except Exception:
        pass
    return {"network": network, "trigger": doc["trigger"], "rules": doc["rules"], "note": doc["note"],
            "live": live, "programs": items}


def state():
    plot_list = plots()["plots"]
    index = _maybe(BATCHES_DIR / "index.json") or {"batches": []}
    return {
        "app": "Krishyak FieldProof",
        "version": __version__,
        "processing_version": PROCESSING_VERSION,
        "analytics_version": ANALYTICS_VERSION,
        "network": default_network(),
        "networks": deployed_networks(),
        "plots": [{k: p[k] for k in ("plot_id", "name", "district", "state", "area_ha", "plot_key", "geometry",
                                     "latest_clear", "chip")} for p in plot_list],
        "batch_count": len(index["batches"]),
        "record_count": sum(b["records"] for b in index["batches"]),
        "sources": [
            {"name": "Sentinel-2 L2A Collection 1", "provider": "ESA Copernicus via Earth Search (Element 84, AWS Open Data)",
             "licence": "Copernicus Sentinel data terms (free, attribution)"},
            {"name": "ERA5 daily rainfall and ET0", "provider": "ECMWF / Copernicus C3S via Open-Meteo",
             "licence": "CC BY 4.0 (Open-Meteo); Copernicus licence (ERA5)"},
        ],
    }


ENDPOINTS = {
    "GET /api/health": "liveness",
    "GET /api/state": "app summary, plots, networks",
    "GET /api/plots": "plots with latest clear observation and stress counts",
    "GET /api/plots/{plot_id}": "plot detail with full time series and field notes",
    "GET /api/plots/{plot_id}/notes?lang=en|hi": "farmer-readable note",
    "GET /api/batches?network=": "monthly batches and their anchors",
    "GET /api/batches/{YYYY-MM}?network=": "batch manifest and attestation",
    "GET /api/records/{record_id}?network=": "record, leaf, proof, root and signature for client-side checks",
    "GET /api/verify/{record_id}?network=&tamper=ndvi=0.9&offline=1&recompute=1": "server-side verification report",
    "GET /api/chain?network=": "chain reachability, block, batch and programme counts",
    "GET /api/contracts?network=": "contract addresses and ABIs",
    "GET /api/programs?network=&plot=": "prototype drought programmes with live on-chain status",
    "POST /rpc/{network}": "read-only JSON-RPC proxy (eth_call, eth_getLogs, ...)",
}
