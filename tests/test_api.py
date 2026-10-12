"""JSON API over the committed sample data (offline; no chain needed)."""
import json
import threading
import urllib.error
import urllib.request

import pytest

from canopyproof import api
from canopyproof.config import SERIES_DIR

pytestmark = pytest.mark.skipif(not (SERIES_DIR / "KE-FIRE-01.json").exists(),
                                reason="sample data not built (python -m canopyproof build)")


def a_record_id(status="reversal_suspected"):
    rows = json.loads((SERIES_DIR / "KE-FIRE-01.json").read_text(encoding="utf-8"))["series"]
    return next(r["record_id"] for r in reversed(rows) if r["status"] == status)


def test_sites_projects_and_detail():
    sites = api.sites()["sites"]
    assert {s["site_id"] for s in sites} >= {"KE-FIRE-01", "KE-STAND-01", "ACR189-01"}
    fire = next(s for s in sites if s["site_id"] == "KE-FIRE-01")
    assert fire["latest"]["status"] == "reversal_suspected" and fire["first_reversal"]["date"].startswith("2021-")
    detail = api.site_detail("KE-FIRE-01")
    assert detail["series"] and detail["project"]["project_id"] == "ACR273"
    assert {p["project_id"] for p in api.projects()["projects"]} == {"ACR273", "ACR189", "DEMO-AMZ"}
    with pytest.raises(api.NotFound):
        api.site_detail("NOPE-00")


def test_record_payload_is_self_contained():
    from canopyproof.canonical import document_hash
    from canopyproof.merkle import verify
    from canopyproof.records import leaf_fields, leaf_hash

    rec = api.record(a_record_id(), network="sepolia")  # a network with no anchor: anchor is None
    assert rec["anchor"] is None
    assert document_hash(rec["document"]) == rec["document_hash"]
    assert leaf_hash(leaf_fields(rec["document"])) == rec["leaf"]
    assert verify(rec["proof"], rec["merkle_root"], rec["leaf"])


def test_offline_verification_and_tamper():
    rid = a_record_id()
    assert api.verify(rid, offline=True)["verified"] is True
    assert api.verify(rid, offline=True, tamper="loss=0.0000")["verified"] is False
    with pytest.raises(api.NotFound):
        api.verify("KE-FIRE-01:missing", offline=True)


def test_http_server_routes_and_read_only_proxy():
    from canopyproof.server import AppServer

    server = AppServer(("127.0.0.1", 0))
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        def get(path):
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}") as r:
                return r.status, json.loads(r.read())

        assert get("/api/health")[1]["ok"] is True
        assert get("/api/sites")[0] == 200
        for path in ("/api/sites/NOPE", "/data/../.env"):
            with pytest.raises(urllib.error.HTTPError) as err:
                urllib.request.urlopen(f"http://127.0.0.1:{port}{path}")
            assert err.value.code in (403, 404)
        req = urllib.request.Request(f"http://127.0.0.1:{port}/rpc/localhost", method="POST",
                                     data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_sendTransaction",
                                                      "params": []}).encode(),
                                     headers={"Content-Type": "application/json"})
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(req)
        assert err.value.code in (403, 404)  # refused (403) or no local deployment (404); never forwarded
    finally:
        server.shutdown()
        server.server_close()
