"""End-to-end against a throw-away Hardhat node: Python leaves/proofs must satisfy the Solidity code.

Skipped automatically when contracts/node_modules or compiled artifacts are missing.
Run only these with `pytest -m chain`; skip them with `pytest -m "not chain"`.
"""
import json
import os
import shutil
import socket
import subprocess
import time
from datetime import date

import pytest

from fieldproof.config import ARTIFACTS_DIR, CONTRACTS_DIR

pytestmark = pytest.mark.chain

NPX = shutil.which("npx.cmd") or shutil.which("npx")
READY = NPX and (CONTRACTS_DIR / "node_modules").exists() and (ARTIFACTS_DIR / "FieldProofRegistry.sol").exists()


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
    import fieldproof.chain as chain
    import fieldproof.config as config
    import fieldproof.records as records

    monkeypatch.setattr(chain, "LOCAL_RPC", node)
    for mod in (chain, records, config):
        monkeypatch.setattr(mod, "BATCHES_DIR", tmp_path / "batches", raising=False)
    for mod in (chain, config):
        monkeypatch.setattr(mod, "CHAIN_DIR", tmp_path / "chain", raising=False)
    monkeypatch.setattr(config, "PROGRAMS_DIR", tmp_path / "programs")
    from synthetic import PLOT, Weather, observations

    from fieldproof.analytics import analyze
    from fieldproof.records import build_batches, build_document

    obs = observations(start=date(2019, 1, 3))
    docs = [build_document(PLOT, o, a) for o, a in zip(obs, analyze(obs, Weather()))]
    docs = [d for d in docs if d["observed_at"] >= "2026-06"]
    build_batches(docs, out_dir=tmp_path / "batches", log=lambda *_: None)
    w3, _ = chain.connect("localhost", require_deployment=False)
    chain.deploy_local(w3)
    w3, deployment = chain.connect("localhost")
    return chain, w3, deployment, docs, tmp_path


def test_anchor_verify_tamper_and_trigger(env):
    chain, w3, deployment, docs, tmp_path = env
    from fieldproof.records import load_entries, observation_tuple
    from fieldproof.signing import recover_leaf_signer
    from fieldproof.verify import verify_record

    attestations = chain.anchor_all(w3, deployment, log=lambda *_: None)
    assert attestations and all(a["mode"].startswith("relayed") for a in attestations)
    registry, trigger = chain.contracts(w3, deployment)

    # Every Python-built leaf and proof is accepted by the Solidity verifier.
    for att in attestations:
        for e in load_entries(att["batch"]):
            proof = [bytes.fromhex(p[2:]) for p in e["proof"]]
            assert registry.functions.verifyObservation(att["batchId"], observation_tuple(e["leaf_fields"]), proof).call()
            assert registry.functions.observationLeaf(observation_tuple(e["leaf_fields"])).call().hex() == e["leaf"][2:]
            assert recover_leaf_signer(e["leaf"], att["recordSignatures"][e["leaf"]]) == deployment["roles"]["attestor"]

    record_id = docs[-1]["record_id"]
    report = verify_record(record_id, network="localhost")
    assert report.ok, report.to_dict()
    tampered = verify_record(record_id, network="localhost", tamper="ndvi=0.9999")
    assert not tampered.ok

    # Anchoring the same root twice is refused on-chain (no double anchoring).
    again = chain.anchor_all(w3, deployment, log=lambda *_: None)
    assert registry.functions.batchCount().call() == len(attestations)
    assert [a["batchId"] for a in again] == [a["batchId"] for a in attestations]

    plot = {"plot_id": "TEST-01", "plot_key": docs[0]["plot"]["plot_key"]}
    out = chain.run_drought_programs(w3, deployment, [plot], [("kharif-2026", "2026-06-01", "2026-10-31")],
                                     log=lambda *_: None)
    program = out["programs"][0]
    assert program["triggered"] and program["qualifyingCount"] == 2
    assert json.loads((tmp_path / "programs" / "localhost.json").read_text())["programs"][0]["triggered"]

    # Revoking a batch makes its records fail verification.
    att = attestations[-1]
    tx = registry.functions.revokeBatch(att["batchId"], "test").transact({"from": deployment["roles"]["admin"]})
    w3.eth.wait_for_transaction_receipt(tx)
    last = load_entries(att["batch"])[0]
    assert not verify_record(last["record_id"], network="localhost").ok
