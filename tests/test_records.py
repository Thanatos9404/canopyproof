import json

from fieldproof.analytics import analyze
from fieldproof.canonical import canonical_bytes, document_hash
from fieldproof.merkle import verify
from fieldproof.records import (
    FLAG_CROP_EXPECTED, FLAG_QUALITY_CLEAR, FLAG_RAIN_AVAILABLE, FLAG_VCI_AVAILABLE, FLAG_ZSCORE_AVAILABLE,
    build_batches, build_document, encode_leaf_fields, leaf_fields, leaf_hash)
from synthetic import PLOT, Weather, observations


def documents():
    obs = observations(start=__import__("datetime").date(2019, 1, 3))
    return [build_document(PLOT, o, a) for o, a in zip(obs, analyze(obs, Weather()))]


def test_documents_are_canonical_and_leaves_follow_the_document():
    docs = documents()
    alert = next(d for d in docs if d["analytics"]["stress_level"] == "alert")
    canonical_bytes(alert)  # raises if any float or non-ASCII value slipped in
    f = leaf_fields(alert)
    assert f["documentHash"] == document_hash(alert)
    assert f["stressLevel"] == 3
    assert f["flags"] & FLAG_QUALITY_CLEAR and f["flags"] & FLAG_RAIN_AVAILABLE
    assert f["flags"] & FLAG_ZSCORE_AVAILABLE and f["flags"] & FLAG_CROP_EXPECTED
    assert bool(f["flags"] & FLAG_VCI_AVAILABLE) == (alert["analytics"]["vci_pct"] is not None)
    assert f["ndviE4"] == round(float(alert["indices"]["ndvi"]["mean"]) * 10_000)
    assert f["rainDeparture60dBps"] == -5000
    assert len(encode_leaf_fields(f)) == 11 * 32


def test_any_edit_changes_the_leaf():
    doc = documents()[-1]
    leaf = leaf_hash(leaf_fields(doc))
    edited = json.loads(json.dumps(doc))
    edited["satellite"]["scene_cloud_cover_pct"] = "1.00"  # not even a leaf field
    assert leaf_hash(leaf_fields(edited)) != leaf


def test_monthly_batches_with_valid_proofs(tmp_path):
    docs = documents()[-60:]
    summaries = build_batches(docs, out_dir=tmp_path, log=lambda *_: None)
    assert sum(s["records"] for s in summaries) == 60
    for s in summaries:
        manifest = json.loads((tmp_path / s["batch"] / "manifest.json").read_text())
        entries = json.loads((tmp_path / s["batch"] / "records.json").read_text())
        assert manifest["merkle_root"] == s["merkle_root"] and manifest["record_count"] == len(entries)
        for e in entries:
            assert e["leaf"] == leaf_hash(leaf_fields(e["document"]))
            assert verify(e["proof"], manifest["merkle_root"], e["leaf"])
