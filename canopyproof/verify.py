"""Independent verification of one record: offline integrity, on-chain anchoring, signer, recompute."""
import copy
import json

from .canonical import document_hash
from .merkle import verify as merkle_verify
from .records import find_entry, leaf_fields, leaf_hash, load_manifest, observation_tuple


class Report:
    def __init__(self, record_id):
        self.record_id = record_id
        self.steps = []

    def add(self, name, ok, detail=""):
        self.steps.append({"step": name, "ok": bool(ok) if ok is not None else None, "detail": detail})
        return ok

    @property
    def ok(self):
        return all(s["ok"] is not False for s in self.steps) and any(s["ok"] for s in self.steps)

    def to_dict(self):
        return {"record_id": self.record_id, "verified": self.ok, "steps": self.steps}


def apply_tamper(document, tamper):
    """`tamper` like 'loss=0.0000' or 'ndvi=0.8123' edits a local copy to show that verification fails."""
    doc = copy.deepcopy(document)
    key, _, value = tamper.partition("=")
    if key == "ndvi" and doc["indices"]["ndvi"]:
        doc["indices"]["ndvi"]["mean"] = value
    elif key == "nbr" and doc["indices"]["nbr"]:
        doc["indices"]["nbr"]["mean"] = value
    elif key == "loss":
        doc["change"]["cumulative_loss_fraction"] = value
        doc["change"]["fractions_bps"]["cumulative"] = int(round(float(value) * 10_000))
    elif key == "status":
        doc["change"]["status"] = value
    else:
        raise ValueError("tamper key must be one of ndvi, nbr, loss, status")
    return doc


def verify_record(record_id, network=None, recompute=False, tamper=None, log=None):
    batch, entry = find_entry(record_id)
    report = Report(record_id)
    doc = apply_tamper(entry["document"], tamper) if tamper else entry["document"]
    if tamper:
        report.add("Tamper simulation", None, f"edited the local copy: {tamper}")

    # 1. The document hashes to the value committed in the leaf.
    dh = document_hash(doc)
    report.add("Document hash (keccak256 of canonical JSON)", dh == entry["document_hash"],
               f"computed {dh[:18]}..., batch file says {entry['document_hash'][:18]}...")
    # 2. Leaf rebuilt from the document alone.
    fields = leaf_fields(doc)
    leaf = leaf_hash(fields)
    report.add("Leaf rebuilt from the document", leaf == entry["leaf"], f"{leaf[:18]}...")
    # 3. Merkle proof to the manifest root.
    manifest, manifest_hash = load_manifest(batch)
    root = manifest["merkle_root"]
    report.add(f"Merkle proof to batch {batch} root", merkle_verify(entry["proof"], root, leaf),
               f"root {root[:18]}..., proof length {len(entry['proof'])}")

    if network:
        from .chain import ChainError, batch_info, connect, contracts, load_attestation
        from .signing import recover_leaf_signer

        try:
            w3, deployment = connect(network)
        except ChainError as exc:
            report.add(f"Connect to {network}", False, str(exc))
            return report
        registry = contracts(w3, deployment)[0]
        batch_id = registry.functions.batchIdByRoot(bytes.fromhex(root[2:])).call()
        if not report.add("Root anchored on-chain", batch_id != 0,
                          f"CanopyProofRegistry {registry.address} on {network}: batch #{batch_id}"):
            return report
        info = batch_info(registry, batch_id)
        report.add("On-chain manifest hash matches", info["manifestHash"] == manifest_hash, info["manifestHash"][:18] + "...")
        report.add("Batch not revoked", not info["revoked"])
        on_chain = registry.functions.verifyObservation(
            batch_id, observation_tuple(fields), [bytes.fromhex(p[2:]) for p in entry["proof"]]).call()
        report.add("Smart contract verifyObservation()", on_chain, "evaluated by the EVM, not by this script")
        attestor_role = registry.functions.ATTESTOR_ROLE().call()
        attestation = load_attestation(batch, network)
        sig = attestation and attestation["recordSignatures"].get(entry["leaf"])
        if sig:
            signer = recover_leaf_signer(leaf, sig)  # the signature must match the leaf we rebuilt
            report.add("Record signature (EIP-191) recovers to the batch attestor",
                       signer == info["attestor"] and registry.functions.hasRole(attestor_role, signer).call(),
                       f"signer {signer}")
        else:
            report.add("Record signature", None, "no local attestation file; on-chain anchoring still verifies")

    if recompute:
        report.add(*_recompute(entry["document"]))
    return report


def _recompute(doc):
    """Re-read the same Sentinel-2 scene and recompute the cell statistics from scratch."""
    import httpx

    from .sentinel import process_item
    from .sites import get_site

    site = get_site(doc["site"]["site_id"])
    item = httpx.get(doc["satellite"]["item_url"], timeout=60).raise_for_status().json()
    fresh = process_item(item, site["geometry"])
    same = (fresh["pixel_digest"] == doc["processing"]["pixel_digest"]
            and fresh["ndvi"] == doc["indices"]["ndvi"] and fresh["nbr"] == doc["indices"]["nbr"]
            and fresh["quality"] == doc["quality"])
    detail = (f"NDVI mean {fresh['ndvi']['mean'] if fresh['ndvi'] else None} vs "
              f"{doc['indices']['ndvi']['mean'] if doc['indices']['ndvi'] else None}; "
              f"pixel digest {'matches' if fresh['pixel_digest'] == doc['processing']['pixel_digest'] else 'differs'}")
    return "Recomputed from the original Sentinel-2 COGs", same, detail


def format_report(report):
    lines = [f"Record {report.record_id}"]
    for s in report.steps:
        mark = {True: "PASS", False: "FAIL", None: "INFO"}[s["ok"]]
        lines.append(f"  [{mark}] {s['step']}" + (f"  - {s['detail']}" if s["detail"] else ""))
    lines.append("VERIFIED" if report.ok else "NOT VERIFIED")
    return "\n".join(lines)


def to_json(report):
    return json.dumps(report.to_dict(), indent=1)
