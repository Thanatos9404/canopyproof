"""End-to-end against a throw-away Hardhat node: Python leaves/proofs must satisfy the Solidity code,
and the integrity oracle must reach the same verdict as the off-chain analytics.

Skipped automatically when contracts/node_modules or compiled artifacts are missing.
Run only these with `pytest -m chain`; skip them with `pytest -m "not chain"`.
"""
import json
import os
import shutil
import socket
import subprocess
import time

import pytest

from canopyproof.config import ARTIFACTS_DIR, CONTRACTS_DIR

pytestmark = pytest.mark.chain

NPX = shutil.which("npx.cmd") or shutil.which("npx")
READY = NPX and (CONTRACTS_DIR / "node_modules").exists() and (ARTIFACTS_DIR / "CanopyProofRegistry.sol").exists()


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def node():
    if not READY:
        pytest.skip("Hardhat not installed/compiled (cd contracts && npm install && npx hardhat compile)")
    port = _free_port()
    proc = subprocess.Popen([NPX, "hardhat", "node", "--port", str(port)], cwd=CONTRACTS_DIR,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    from web3 import Web3

    w3 = Web3(Web3.HTTPProvider(url))
    for _ in range(120):
        try:
            if w3.eth.chain_id == 31337:
                break
        except Exception:
            time.sleep(0.5)
    else:
        proc.kill()
        pytest.fail("hardhat node did not start")
    yield url
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    else:
        proc.terminate()


@pytest.fixture()
def env(node, tmp_path, monkeypatch):
    import canopyproof.chain as chain
    import canopyproof.config as config
    import canopyproof.records as records

    monkeypatch.setattr(chain, "LOCAL_RPC", node)
    for mod in (chain, records, config):
        monkeypatch.setattr(mod, "BATCHES_DIR", tmp_path / "batches", raising=False)
    for mod in (chain, config):
        monkeypatch.setattr(mod, "CHAIN_DIR", tmp_path / "chain", raising=False)
    monkeypatch.setattr(config, "INTEGRITY_DIR", tmp_path / "integrity")
    from synthetic import BASELINE, MONTHS, SITE, scenes

    from canopyproof.forest import analyze_site
    from canopyproof.records import build_batches, build_document

    obs, rasters = scenes()
    res, _ = analyze_site(obs, rasters, *BASELINE, monitoring_months=MONTHS)
    docs = [build_document(SITE, o, a) for o, a in zip(obs, res)]
    build_batches(docs, out_dir=tmp_path / "batches", log=lambda *_: None)
    w3, _ = chain.connect("localhost", require_deployment=False)
    chain.deploy_local(w3)
    w3, deployment = chain.connect("localhost")
    site = {**SITE, "land_cells": [{"key": "0x" + "12" * 32}, {"key": "0x" + "34" * 32}]}
    project = {"project_id": SITE["project_id"], "project_key": SITE["project_key"], "sites": [site],
               "credit_batch": {"vintage_start": 2018, "vintage_end": 2022, "issued_credits": 10_000}}
    relist = {"project_id": "RELIST", "note": "test", "over_site": SITE["site_id"], "rejected_vintages": [2020, 2023],
              "accepted_vintages": [2024, 2026], "issued_credits": 100}
    return chain, w3, deployment, docs, project, relist, site


def test_anchor_verify_integrity_and_ledger(env):
    chain, w3, deployment, docs, project, relist, site = env
    from canopyproof.records import load_entries, observation_tuple
    from canopyproof.signing import recover_leaf_signer
    from canopyproof.verify import verify_record

    attestations = chain.anchor_all(w3, deployment, log=lambda *_: None)
    assert attestations and all(a["mode"].startswith("relayed") for a in attestations)
    registry, oracle, _ = chain.contracts(w3, deployment)

    # Every Python-built leaf and proof is accepted by the Solidity verifier.
    for att in attestations:
        for e in load_entries(att["batch"]):
            obs = observation_tuple(e["leaf_fields"])
            assert registry.functions.verifyObservation(att["batchId"], obs, [bytes.fromhex(p[2:]) for p in e["proof"]]).call()
            assert "0x" + registry.functions.observationLeaf(obs).call().hex() == e["leaf"]
            assert recover_leaf_signer(e["leaf"], att["recordSignatures"][e["leaf"]]) == deployment["roles"]["attestor"]

    rid = next(d["record_id"] for d in docs if d["change"]["status"] == "reversal_suspected")
    assert verify_record(rid, network="localhost").ok
    assert not verify_record(rid, network="localhost", tamper="loss=0.0000").ok

    # Anchoring the same roots again is refused on-chain (no double anchoring).
    chain.anchor_all(w3, deployment, log=lambda *_: None)
    assert registry.functions.batchCount().call() == len(attestations)

    out = chain.run_integrity(w3, deployment, [project], relist, [site], log=lambda *_: None)
    cb = out["credit_batches"][0]
    assert cb["status"] == "reversal_suspected" and cb["buffer_review_flagged"] is True
    assert cb["indicative_affected_bps"] == 5000 and cb["indicative_affected_credits"] == 5000
    statuses = [e["status"] for e in cb["evidence"]]
    assert statuses[0] == "healthy" and "watch" in statuses and statuses[-1] == "reversal_suspected"
    assert out["relisting_demo"]["overlapping"]["error"] == "LandAlreadyClaimed"
    assert out["relisting_demo"]["later_vintages"]["accepted"] is True
    assert out["ledger_demo"][0]["retired"] is False and out["ledger_demo"][0]["check"] == 4
    assert json.loads((chain.CHAIN_DIR.parent / "integrity" / "localhost.json").read_text())["credit_batches"]
