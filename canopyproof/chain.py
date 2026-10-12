"""Talking to FieldProofRegistry and ParametricDroughtTrigger with web3.py.

Only chain IDs in config.ALLOWED_CHAINS (local Hardhat, Sepolia, Polygon Amoy) are accepted.
"""
import json
import os
from datetime import datetime, timezone

from web3 import Web3

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
        raise ChainError(f"No deployment for '{network}'. Run `fieldproof deploy` (local) or the Hardhat deploy script.")
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
        addr = deployment["contracts"]["FieldProofRegistry"]["address"]
        if deployment["chainId"] != chain_id or len(w3.eth.get_code(addr)) == 0:
            raise ChainError(f"No contract at {addr} on {network}. The local chain was probably restarted; "
                             "run `fieldproof deploy` again.")
    return w3, deployment


def contracts(w3, deployment):
    c = deployment["contracts"]
    registry = w3.eth.contract(address=c["FieldProofRegistry"]["address"], abi=c["FieldProofRegistry"]["abi"])
    trigger = w3.eth.contract(address=c["ParametricDroughtTrigger"]["address"], abi=c["ParametricDroughtTrigger"]["abi"])
    return registry, trigger


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
    """Deploy both contracts with the Hardhat node's dev accounts and write data/chain/localhost.json."""
    if w3.eth.chain_id != 31337:
        raise ChainError("deploy_local only targets the local Hardhat chain; use scripts/deploy.js for testnets")
    accounts = w3.eth.accounts
    admin, attestor, relayer, program_admin, farmer = accounts[:5]
    reg_art, trg_art = _artifact("FieldProofRegistry"), _artifact("ParametricDroughtTrigger")

    def deploy(art, *args):
        factory = w3.eth.contract(abi=art["abi"], bytecode=art["bytecode"])
        receipt = w3.eth.wait_for_transaction_receipt(factory.constructor(*args).transact({"from": admin}))
        return w3.eth.contract(address=receipt.contractAddress, abi=art["abi"]), receipt

    registry, reg_receipt = deploy(reg_art, admin)
    trigger, _ = deploy(trg_art, registry.address, admin)
    _send(w3, registry.functions.grantRole(registry.functions.ATTESTOR_ROLE().call(), attestor), admin)
    _send(w3, trigger.functions.grantRole(trigger.functions.PROGRAM_ADMIN_ROLE().call(), program_admin), admin)
    out = {
        "network": network,
        "chainId": 31337,
        "rpcUrl": LOCAL_RPC,
        "explorer": None,
        "deployedAt": _now(),
        "deployBlock": reg_receipt.blockNumber,
        "roles": {"admin": admin, "attestor": attestor, "programAdmin": program_admin},
        "accounts": {"relayer": relayer, "farmer": farmer},
        "note": "Hardhat dev accounts from the public test mnemonic. Worthless outside the local chain.",
        "contracts": {
            "FieldProofRegistry": {"address": registry.address, "abi": reg_art["abi"]},
            "ParametricDroughtTrigger": {"address": trigger.address, "abi": trg_art["abi"]},
        },
    }
    CHAIN_DIR.mkdir(parents=True, exist_ok=True)
    deployment_path(network).write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    for batch_dir in BATCHES_DIR.glob("*/"):
        stale = batch_dir / f"attestation.{network}.json"
        if stale.exists():
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
    registry, _ = contracts(w3, deployment)
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
    event = registry.events.BatchAnchored().process_receipt(receipt)[0]["args"]
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
    attestation_path(batch, network).write_text(json.dumps(attestation, indent=1) + "\n", encoding="utf-8")
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


# ----------------------------------------------------------------------------- drought programmes

PROGRAM_DEFAULTS = {"maxVciBps": 3500, "maxNdviZE2": -200, "maxRainDepartureBps": -2000,
                    "minValidFractionBps": 6000, "requiredObservations": 2}
REASONS = {0: "qualifies", 1: "different plot", 2: "outside programme window", 3: "not cloud-free",
           4: "too few valid pixels", 5: "no vegetation anomaly measure (off-season or short history)",
           6: "vegetation not anomalously low",
           7: "rainfall departure unavailable", 8: "rainfall not deficient enough", 9: "already counted",
           10: "programme already triggered"}


def _ts(day):
    return int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp())


def run_drought_programs(w3, deployment, plots, seasons, log=print):
    """Create one prototype programme per plot and season, then submit every anchored qualifying record."""
    network = deployment["network"]
    _, trigger = contracts(w3, deployment)
    admin = deployment["roles"]["programAdmin"]
    submitter = deployment.get("accounts", {}).get("farmer") or admin
    if deployment["chainId"] != 31337:
        attestor_signer(w3, deployment)  # installs the env-key signing middleware for testnets

    index = json.loads((BATCHES_DIR / "index.json").read_text(encoding="utf-8"))
    entries_by_plot = {}
    for summary in index["batches"]:
        att = load_attestation(summary["batch"], network)
        if not att:
            continue
        for e in load_entries(summary["batch"]):
            entries_by_plot.setdefault(e["document"]["plot"]["plot_id"], []).append((att["batchId"], e))

    results = []
    for plot in plots:
        for label, start, end in seasons:
            terms = (f"PROTOTYPE {label} drought trigger for {plot['plot_id']}: (VCI < 35 or in-season NDVI z <= -2) "
                     "and 60-day ERA5 rainfall <= -20% of the 1991-2020 normal, on "
                     f"{PROGRAM_DEFAULTS['requiredObservations']} cloud-free acquisitions. No funds; illustrative thresholds.")
            p_input = (bytes.fromhex(plot["plot_key"][2:]), _ts(start), _ts(end) + 86399,
                       PROGRAM_DEFAULTS["maxVciBps"], PROGRAM_DEFAULTS["maxNdviZE2"],
                       PROGRAM_DEFAULTS["maxRainDepartureBps"],
                       PROGRAM_DEFAULTS["minValidFractionBps"], PROGRAM_DEFAULTS["requiredObservations"], terms)
            receipt = _send(w3, trigger.functions.createProgram(p_input), admin)
            program_id = trigger.events.ProgramCreated().process_receipt(receipt)[0]["args"]["programId"]
            accepted, rejected = [], {}
            for batch_id, e in sorted(entries_by_plot.get(plot["plot_id"], []), key=lambda x: x[1]["leaf_fields"]["observedAt"]):
                fields = e["leaf_fields"]
                if not (_ts(start) <= fields["observedAt"] <= _ts(end) + 86399):
                    continue
                obs = observation_tuple(fields)
                code = trigger.functions.evaluate(program_id, obs).call()
                if code != 0:
                    rejected[REASONS[code]] = rejected.get(REASONS[code], 0) + 1
                    if code == 10:
                        break
                    continue
                proof = [bytes.fromhex(p[2:]) for p in e["proof"]]
                r = _send(w3, trigger.functions.submitEvidence(program_id, batch_id, obs, proof), submitter)
                accepted.append({"record_id": e["record_id"], "observed_at": e["document"]["observed_at"],
                                 "vci_pct": e["document"]["analytics"]["vci_pct"],
                                 "ndvi_z": e["document"]["analytics"]["ndvi_residual_z"],
                                 "rain_60d_departure_pct": e["document"]["weather"].get("rain_60d_departure_pct"),
                                 "batchId": batch_id, "txHash": _hex(r.transactionHash)})
            prog = trigger.functions.getProgram(program_id).call()
            # Program tuple: plotKey, windowStart, windowEnd, maxVciBps, maxNdviZE2, maxRainDepartureBps,
            # minValidFractionBps, requiredObservations, qualifyingCount, triggered, triggeredAt, creator, terms
            required, count, triggered, triggered_at = prog[7], prog[8], prog[9], prog[10]
            result = {"programId": program_id, "plot_id": plot["plot_id"], "season": label, "window": [start, end],
                      "triggered": triggered, "triggeredAt": triggered_at or None, "qualifyingCount": count,
                      "required": required, "accepted": accepted, "not_qualifying": rejected, "terms": terms}
            results.append(result)
            state = "TRIGGERED" if triggered else "not triggered"
            log(f"  programme #{program_id} {plot['plot_id']} {label}: {state} ({count}/{required} qualifying)")
    from .config import PROGRAMS_DIR

    PROGRAMS_DIR.mkdir(parents=True, exist_ok=True)
    out = {"network": network, "trigger": trigger.address, "created_at": _now(), "rules": PROGRAM_DEFAULTS,
           "note": "Prototype parametric trigger. Back-test programmes were created after their season.",
           "programs": results}
    (PROGRAMS_DIR / f"{network}.json").write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    return out
