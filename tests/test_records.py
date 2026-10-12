import json

from canopyproof.canonical import canonical_bytes, document_hash
from canopyproof.forest import analyze_site
from canopyproof.merkle import verify
from canopyproof.records import (FLAG_BASELINE_READY, FLAG_CLEAR, build_batches, build_document, encode_leaf_fields,
                                 leaf_fields, leaf_hash)
from synthetic import BASELINE, MONTHS, SITE, scenes


def documents():
    obs, rasters = scenes()
    res, _ = analyze_site(obs, rasters, *BASELINE, monitoring_months=MONTHS)
    return [build_document(SITE, o, a) for o, a in zip(obs, res)]


def test_documents_are_canonical_and_leaves_follow_the_document():
    docs = documents()
    rev = next(d for d in docs if d["change"]["status"] == "reversal_suspected")
    canonical_bytes(rev)  # raises if any float or non-ASCII value slipped in
    f = leaf_fields(rev)
    assert f["documentHash"] == document_hash(rev)
    assert f["status"] == 3 and f["flags"] & FLAG_CLEAR and f["flags"] & FLAG_BASELINE_READY
    assert f["cumulativeLossBps"] == 5000 and f["siteKey"] == SITE["site_key"] and f["projectKey"] == SITE["project_key"]
    assert f["ndviE4"] == round(float(rev["indices"]["ndvi"]["mean"]) * 10_000)
    assert len(encode_leaf_fields(f)) == 13 * 32


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
