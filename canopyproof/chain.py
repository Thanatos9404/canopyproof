"""Talking to CanopyProofRegistry, CarbonIntegrityOracle and GuardedCreditLedger with web3.py.

Only chain IDs in config.ALLOWED_CHAINS (local Hardhat, Sepolia, Polygon Amoy) are accepted.
"""
import json
import os
from datetime import datetime, timezone

from web3 import Web3
from web3.logs import DISCARD

from .config import ALLOWED_CHAINS, ARTIFACTS_DIR, BATCHES_DIR, CHAIN_DIR, ROOT
from .records import load_entries, load_manifest, observation_tuple
from .signing import RpcSigner, batch_typed_data

LOCAL_RPC = os.environ.get("FIELDPROOF_RPC_URL", "http://127.0.0.1:8545")


class ChainError(RuntimeError):
    pass


def _hex(b):
    return "0x" + bytes(b).hex()


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def deployment_path(network):
    return CHAIN_DIR / f"{network}.json"


def load_deployment(network):
    path = deployment_path(network)
    if not path.exists():
        raise ChainError(f"No deployment for '{network}'. Run `canopyproof deploy` (local) or the Hardhat deploy script.")
    return json.loads(path.read_text(encoding="utf-8"))


def rpc_url_for(network, deployment=None):
    if network == "localhost":
        return LOCAL_RPC
    from dotenv import dotenv_values

    env = {**dotenv_values(ROOT / ".env"), **os.environ}
    defaults = {"sepolia": "https://ethereum-sepolia-rpc.publicnode.com", "amoy": "https://rpc-amoy.polygon.technology"}
    return env.get(f"{network.upper()}_RPC_URL") or defaults[network]


def connect(network="localhost", rpc_url=None, require_deployment=True):
    if network not in ALLOWED_CHAINS.values():
        raise ChainError(f"Unknown network '{network}'. Allowed: {', '.join(ALLOWED_CHAINS.values())}")
    w3 = Web3(Web3.HTTPProvider(rpc_url or rpc_url_for(network), request_kwargs={"timeout": 30}))
    try:
        chain_id = w3.eth.chain_id
    except Exception as exc:
        hint = " Start it with `cd contracts && npx hardhat node`." if network == "localhost" else ""
        raise ChainError(f"Cannot reach the {network} RPC.{hint}") from exc
    if chain_id not in ALLOWED_CHAINS:
        raise ChainError(f"Refusing chain id {chain_id}: only local and public testnets are allowed.")
    if ALLOWED_CHAINS[chain_id] != network:
        raise ChainError(f"RPC is chain {chain_id} ({ALLOWED_CHAINS[chain_id]}), not {network}.")
    deployment = None
    if require_deployment:
        deployment = load_deployment(network)
        addr = deployment["contracts"]["CanopyProofRegistry"]["address"]
        if deployment["chainId"] != chain_id or len(w3.eth.get_code(addr)) == 0:
            raise ChainError(f"No contract at {addr} on {network}. The local chain was probably restarted; "
                             "run `canopyproof deploy` again.")
    return w3, deployment


CONTRACT_NAMES = ("CanopyProofRegistry", "CarbonIntegrityOracle", "GuardedCreditLedger")


def contracts(w3, deployment):
    """(registry, oracle, ledger) web3 contract objects."""
    c = deployment["contracts"]
    return tuple(w3.eth.contract(address=c[n]["address"], abi=c[n]["abi"]) for n in CONTRACT_NAMES)


def _artifact(name):
    path = ARTIFACTS_DIR / f"{name}.sol" / f"{name}.json"
    if not path.exists():
        raise ChainError("Contracts are not compiled. Run `cd contracts && npm install && npx hardhat compile`.")
    return json.loads(path.read_text(encoding="utf-8"))


def _send(w3, fn, sender):
    tx = fn.transact({"from": sender})
    receipt = w3.eth.wait_for_transaction_receipt(tx, timeout=180)
    if receipt.status != 1:
        raise ChainError(f"transaction {tx.hex()} reverted")
    return receipt


def deploy_local(w3, network="localhost"):
    """Deploy all contracts with the Hardhat node's dev accounts and write data/chain/localhost.json."""
    if w3.eth.chain_id != 31337:
        raise ChainError("deploy_local only targets the local Hardhat chain; use scripts/deploy.js for testnets")
    accounts = w3.eth.accounts
    admin, attestor, relayer, registrar, watchdog, auditor, buyer = accounts[:7]
    arts = {n: _artifact(n) for n in CONTRACT_NAMES}

    def deploy(name, *args):
        factory = w3.eth.contract(abi=arts[name]["abi"], bytecode=arts[name]["bytecode"])
        receipt = w3.eth.wait_for_transaction_receipt(factory.constructor(*args).transact({"from": admin}))
        return w3.eth.contract(address=receipt.contractAddress, abi=arts[name]["abi"]), receipt

    registry, reg_receipt = deploy("CanopyProofRegistry", admin)
    oracle, _ = deploy("CarbonIntegrityOracle", registry.address, admin)
    ledger, _ = deploy("GuardedCreditLedger", oracle.address, admin)
    _send(w3, registry.functions.grantRole(registry.functions.ATTESTOR_ROLE().call(), attestor), admin)
    _send(w3, oracle.functions.grantRole(oracle.functions.REGISTRAR_ROLE().call(), registrar), admin)
    _send(w3, oracle.functions.grantRole(oracle.functions.AUDITOR_ROLE().call(), auditor), admin)
    _send(w3, ledger.functions.grantRole(ledger.functions.ISSUER_ROLE().call(), registrar), admin)
    out = {
        "network": network,
        "chainId": 31337,
        "rpcUrl": LOCAL_RPC,
        "explorer": None,
        "deployedAt": _now(),
        "deployBlock": reg_receipt.blockNumber,
        "roles": {"admin": admin, "attestor": attestor, "registrar": registrar, "auditor": auditor},
        "accounts": {"relayer": relayer, "watchdog": watchdog, "buyer": buyer},
        "note": "Hardhat dev accounts from the public test mnemonic. Worthless outside the local chain.",
        "contracts": {n: {"address": c.address, "abi": arts[n]["abi"]}
                      for n, c in zip(CONTRACT_NAMES, (registry, oracle, ledger))},
    }
    CHAIN_DIR.mkdir(parents=True, exist_ok=True)
    deployment_path(network).write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8", newline="\n")
    for stale in list(BATCHES_DIR.glob(f"*/attestation.{network}.json")):
        stale.unlink()
    return out


def attestor_signer(w3, deployment):
    """Local: the node's dev account. Testnets: the key from the git-ignored .env."""
    if deployment["chainId"] == 31337:
        return RpcSigner(w3, deployment["roles"]["attestor"])
    from .signing import EnvKeySigner

    signer = EnvKeySigner()
    from web3.middleware import SignAndSendRawMiddlewareBuilder

    w3.middleware_onion.inject(SignAndSendRawMiddlewareBuilder.build(signer.account), layer=0)
    return signer


# ----------------------------------------------------------------------------- anchoring

def attestation_path(batch, network):
    return BATCHES_DIR / batch / f"attestation.{network}.json"


def load_attestation(batch, network):
    path = attestation_path(batch, network)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def anchor_batch(w3, deployment, batch, signer, relayer=None, log=print):
    """Sign every record leaf (EIP-191) and the batch (EIP-712), then anchor the Merkle root."""
    network = deployment["network"]
    registry = contracts(w3, deployment)[0]
    manifest, manifest_hash = load_manifest(batch)
    root = manifest["merkle_root"]
    existing = registry.functions.batchIdByRoot(bytes.fromhex(root[2:])).call()
    if existing:
        log(f"  {batch}: root already anchored as batch #{existing}")
        return load_attestation(batch, network)

    entries = load_entries(batch)
    record_signatures = {e["leaf"]: signer.sign_leaf(e["leaf"]) for e in entries}
    uri = f"data/batches/{batch}/manifest.json"
    batch_input = {
        "merkleRoot": root,
        "manifestHash": manifest_hash,
        "recordCount": manifest["record_count"],
        "periodStart": manifest["period_start_unix"],
        "periodEnd": manifest["period_end_unix"],
        "uri": uri,
    }
    as_tuple = (bytes.fromhex(root[2:]), bytes.fromhex(manifest_hash[2:]), batch_input["recordCount"],
                batch_input["periodStart"], batch_input["periodEnd"], uri)
    nonce = registry.functions.nonces(signer.address).call()
    typed = batch_typed_data(deployment["chainId"], registry.address, batch_input, nonce)
    batch_signature = signer.sign_typed(typed)
    digest = registry.functions.batchDigest(as_tuple, nonce).call()
    if relayer:
        receipt = _send(w3, registry.functions.anchorBatchWithSignature(
            as_tuple, signer.address, bytes.fromhex(batch_signature[2:])), relayer)
        mode = "relayed (EIP-712 signature, relayer paid gas)"
    else:
        receipt = _send(w3, registry.functions.anchorBatch(as_tuple), signer.address)
        mode = "direct (attestor transaction)"
    event = registry.events.BatchAnchored().process_receipt(receipt, errors=DISCARD)[0]["args"]
    attestation = {
        "network": network,
        "chainId": deployment["chainId"],
        "registry": registry.address,
        "batch": batch,
        "batchId": event["batchId"],
        "merkleRoot": root,
        "manifestHash": manifest_hash,
        "attestor": signer.address,
        "mode": mode,
        "submitter": event["submitter"],
        "txHash": _hex(receipt.transactionHash),
        "blockNumber": receipt.blockNumber,
        "gasUsed": receipt.gasUsed,
        "eip712Digest": _hex(digest),
        "batchSignature": batch_signature,
        "recordSignatures": record_signatures,
        "anchoredAt": _now(),
    }
    attestation_path(batch, network).write_text(json.dumps(attestation, indent=1) + "\n", encoding="utf-8", newline="\n")
    log(f"  {batch}: {manifest['record_count']:3d} records -> batch #{event['batchId']} "
        f"(block {receipt.blockNumber}, gas {receipt.gasUsed})")
    return attestation


def anchor_all(w3, deployment, batches=None, relayed=True, log=print):
    signer = attestor_signer(w3, deployment)
    relayer = deployment.get("accounts", {}).get("relayer") if relayed else None
    index = json.loads((BATCHES_DIR / "index.json").read_text(encoding="utf-8"))
    names = [b["batch"] for b in index["batches"] if not batches or b["batch"] in batches]
    log(f"anchoring {len(names)} batches on {deployment['network']} as attestor {signer.address}"
        + (f" via relayer {relayer}" if relayer else ""))
    return [anchor_batch(w3, deployment, name, signer, relayer=relayer, log=log) for name in names]


def batch_info(registry, batch_id):
    b = registry.functions.getBatch(batch_id).call()
    keys = ["merkleRoot", "manifestHash", "attestor", "periodStart", "periodEnd", "anchoredAt", "recordCount",
            "revoked", "uri"]
    info = dict(zip(keys, b))
    info["merkleRoot"] = _hex(info["merkleRoot"])
    info["manifestHash"] = _hex(info["manifestHash"])
    return info


# ----------------------------------------------------------------------------- credit batches & integrity

STATUS = ["unmonitored", "healthy", "watch", "reversal_suspected"]
RETIREMENT_CHECK = {0: "ok to retire", 1: "not yet monitored", 2: "evidence is stale", 3: "watch: review before retiring",
                    4: "reversal suspected: transfers frozen"}
WATCH_BPS, REVERSAL_BPS, MAX_EVIDENCE_AGE = 200, 500, 60 * 86400


def _b32(h):
    return bytes.fromhex(h[2:])


def _batch_tuple(project_key, ref, v0, v1, credits, site_keys, land_cells, uri):
    return (_b32(project_key), ref, v0, v1, credits, [_b32(k) for k in site_keys], [_b32(c) for c in land_cells],
            WATCH_BPS, REVERSAL_BPS, MAX_EVIDENCE_AGE, uri)


def _land_cells(sites):
    seen, out = set(), []
    for s in sites:
        for c in s["land_cells"]:
            if c["key"] not in seen:
                seen.add(c["key"])
                out.append(c["key"])
    return out


def _custom_error(contract, exc):
    """Decode a custom error such as LandAlreadyClaimed from a reverted eth_call."""
    from eth_abi import decode
    from eth_utils import function_abi_to_4byte_selector

    data = getattr(exc, "data", None)
    if isinstance(data, dict):  # Hardhat via web3: {"message": ..., "data": "0x<selector><args>"}
        data = data.get("data")
    if isinstance(data, dict):
        data = data.get("data")
    if not isinstance(data, str):
        data = next((a for a in exc.args if isinstance(a, str) and a.startswith("0x")), "")
    for item in contract.abi:
        if item.get("type") != "error":
            continue
        selector = "0x" + function_abi_to_4byte_selector(item).hex()
        if data.startswith(selector):
            values = decode([i["type"] for i in item["inputs"]], bytes.fromhex(data[10:]))
            return item["name"], ["0x" + v.hex() if isinstance(v, bytes) else v for v in values]
    return type(exc).__name__, [str(exc)[:200]]


def register_credit_batches(w3, deployment, projects, log=print):
    oracle = contracts(w3, deployment)[1]
    registrar = deployment["roles"]["registrar"]
    out = []
    for p in projects:
        cb = p["credit_batch"]
        cells = _land_cells(p["sites"])
        args = _batch_tuple(p["project_key"], p["project_id"], cb["vintage_start"], cb["vintage_end"], cb["issued_credits"],
                            [s["site_key"] for s in p["sites"]], cells, f"data/projects.json#{p['project_id']}")
        receipt = _send(w3, oracle.functions.registerCreditBatch(args), registrar)
        ev = oracle.events.CreditBatchRegistered().process_receipt(receipt, errors=DISCARD)[0]["args"]
        out.append({"project_id": p["project_id"], "creditBatchId": ev["batchId"], "txHash": _hex(receipt.transactionHash),
                    "vintages": [cb["vintage_start"], cb["vintage_end"]], "issued_credits": cb["issued_credits"],
                    "sites": [s["site_id"] for s in p["sites"]], "land_cells": len(cells), "gasUsed": receipt.gasUsed})
        log(f"  credit batch #{ev['batchId']} {p['project_id']} vintages {cb['vintage_start']}-{cb['vintage_end']}, "
            f"{len(p['sites'])} monitoring cells, {len(cells)} land cells")
    return out


def relisting_demo(w3, deployment, relist, sites, log=print):
    """Try to re-list the same land for overlapping vintages (refused) and for later vintages (accepted)."""
    from .sites import project_key

    oracle = contracts(w3, deployment)[1]
    registrar = deployment["roles"]["registrar"]
    site = next(s for s in sites if s["site_id"] == relist["over_site"])

    def args(v0, v1):
        return _batch_tuple(project_key(relist["project_id"]), relist["project_id"], v0, v1, relist["issued_credits"],
                            [site["site_key"]], _land_cells([site]), "data/projects.json#relisting_demo")

    result = {"project_id": relist["project_id"], "note": relist["note"], "over_site": site["site_id"]}
    v0, v1 = relist["rejected_vintages"]
    try:
        oracle.functions.registerCreditBatch(args(v0, v1)).call({"from": registrar})
        result["overlapping"] = {"vintages": [v0, v1], "accepted": True}
    except Exception as exc:
        name, values = _custom_error(oracle, exc)
        result["overlapping"] = {"vintages": [v0, v1], "accepted": False, "error": name, "values": values}
        log(f"  re-listing {v0}-{v1} over {site['site_id']}: refused on-chain with {name} {values[1:]}")
    v0, v1 = relist["accepted_vintages"]
    receipt = _send(w3, oracle.functions.registerCreditBatch(args(v0, v1)), registrar)
    ev = oracle.events.CreditBatchRegistered().process_receipt(receipt, errors=DISCARD)[0]["args"]
    result["later_vintages"] = {"vintages": [v0, v1], "accepted": True, "creditBatchId": ev["batchId"],
                                "txHash": _hex(receipt.transactionHash)}
    log(f"  re-listing {v0}-{v1}: accepted as credit batch #{ev['batchId']} (no vintage overlap)")
    return result


def submit_integrity_evidence(w3, deployment, credit_batches, projects, mode="changes", log=print):
    """Submit anchored observations to the oracle as a public watchdog would.

    mode="changes" submits, per cell and in time order, each observation whose contract-computed
    status differs from the last submitted one, plus the latest measurable observation.
    """
    oracle = contracts(w3, deployment)[1]
    watchdog = deployment.get("accounts", {}).get("watchdog") or deployment["roles"]["admin"]
    network = deployment["network"]
    index = json.loads((BATCHES_DIR / "index.json").read_text(encoding="utf-8"))
    by_site = {}
    for summary in index["batches"]:
        att = load_attestation(summary["batch"], network)
        if not att:
            continue
        for e in load_entries(summary["batch"]):
            by_site.setdefault(e["document"]["site"]["site_id"], []).append((att["batchId"], e))
    results = []
    for cb in credit_batches:
        project = next(p for p in projects if p["project_id"] == cb["project_id"])
        timeline = []
        for site in project["sites"]:
            entries = sorted(by_site.get(site["site_id"], []), key=lambda x: x[1]["leaf_fields"]["observedAt"])
            measurable = [(b, e) for b, e in entries if e["leaf_fields"]["flags"] & 3 == 3]
            last_status = None
            for i, (reg_batch, e) in enumerate(measurable):
                obs = observation_tuple(e["leaf_fields"])
                status = oracle.functions.previewStatus(cb["creditBatchId"], obs).call()
                if mode == "changes" and status == last_status and i != len(measurable) - 1:
                    continue
                proof = [_b32(x) for x in e["proof"]]
                receipt = _send(w3, oracle.functions.submitEvidence(cb["creditBatchId"], reg_batch, obs, proof), watchdog)
                accepted = oracle.events.EvidenceAccepted().process_receipt(receipt, errors=DISCARD)[0]["args"]
                flagged = oracle.events.BufferPoolReviewFlagged().process_receipt(receipt, errors=DISCARD)
                last_status = status
                timeline.append({"site_id": site["site_id"], "record_id": e["record_id"],
                                 "observed_at": e["document"]["observed_at"], "status": STATUS[accepted["status"]],
                                 "cumulative_loss_bps": accepted["cumulativeLossBps"], "registryBatchId": reg_batch,
                                 "txHash": _hex(receipt.transactionHash), "buffer_pool_review_flagged": bool(flagged)})
        st = oracle.functions.integrityStatus(cb["creditBatchId"]).call()
        check = oracle.functions.retirementCheck(cb["creditBatchId"]).call()
        affected = oracle.functions.indicativeAffectedCredits(cb["creditBatchId"]).call()
        batch = oracle.functions.getCreditBatch(cb["creditBatchId"]).call()
        results.append({**cb, "status": STATUS[st[0]], "oldest_observation": st[1], "stale": st[2],
                        "indicative_affected_bps": st[3], "sites_monitored": st[4],
                        "indicative_affected_credits": affected, "buffer_review_flagged": batch[9],
                        "retirement_check": check, "retirement_check_text": RETIREMENT_CHECK[check],
                        "evidence": timeline})
        log(f"  credit batch #{cb['creditBatchId']} {cb['project_id']}: {STATUS[st[0]].upper()} "
            f"({len(timeline)} evidence tx; retirement: {RETIREMENT_CHECK[check]})")
    return results


def ledger_demo(w3, deployment, results, log=print):
    """Mint a few example credits per batch to the buyer account and try to retire them."""
    ledger = contracts(w3, deployment)[2]
    registrar = deployment["roles"]["registrar"]
    buyer = deployment.get("accounts", {}).get("buyer") or registrar
    outcomes = []
    for r in results:
        _send(w3, ledger.functions.mint(r["creditBatchId"], buyer, 100), registrar)
        fn = ledger.functions.retire(r["creditBatchId"], 10, "Example buyer, demo retirement")
        try:
            fn.call({"from": buyer})
        except Exception as exc:
            name, values = _custom_error(ledger, exc)
            code = values[0] if values and isinstance(values[0], int) else None
            outcomes.append({"creditBatchId": r["creditBatchId"], "project_id": r["project_id"], "retired": False,
                             "error": name, "check": code, "reason": RETIREMENT_CHECK.get(code)})
            log(f"  ledger: retiring batch #{r['creditBatchId']} blocked by {name}({code}): {RETIREMENT_CHECK.get(code)}")
            continue
        receipt = _send(w3, fn, buyer)
        outcomes.append({"creditBatchId": r["creditBatchId"], "project_id": r["project_id"], "retired": True,
                         "txHash": _hex(receipt.transactionHash)})
        log(f"  ledger: retired 10 example credits of batch #{r['creditBatchId']}")
    return outcomes


def run_integrity(w3, deployment, projects, relist, sites, log=print):
    from .config import INTEGRITY_DIR

    if deployment["chainId"] != 31337:
        attestor_signer(w3, deployment)  # testnets: sign locally with the key from the git-ignored .env
    log("registering credit batches")
    batches = register_credit_batches(w3, deployment, projects, log=log)
    log("double-claim check")
    relisting = relisting_demo(w3, deployment, relist, sites, log=log)
    log("submitting anchored evidence as a public watchdog")
    results = submit_integrity_evidence(w3, deployment, batches, projects, log=log)
    log("example marketplace (GuardedCreditLedger)")
    ledger = ledger_demo(w3, deployment, results, log=log)
    out = {"network": deployment["network"], "created_at": _now(),
           "thresholds": {"watch_bps": WATCH_BPS, "reversal_bps": REVERSAL_BPS,
                          "max_evidence_age_days": MAX_EVIDENCE_AGE // 86400},
           "credit_batches": results, "relisting_demo": relisting, "ledger_demo": ledger}
    INTEGRITY_DIR.mkdir(parents=True, exist_ok=True)
    (INTEGRITY_DIR / f"{deployment['network']}.json").write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8", newline="\n")
    return out
