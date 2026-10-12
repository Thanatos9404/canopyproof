"""JSON API over the committed sample data (offline; no chain needed)."""
import json
import threading
import urllib.request

import pytest

from fieldproof import api
from fieldproof.config import SERIES_DIR

pytestmark = pytest.mark.skipif(not (SERIES_DIR / "IN-AP-ATP-01.json").exists(),
                                reason="sample data not built (python -m fieldproof build)")


def a_record_id():
    rows = json.loads((SERIES_DIR / "IN-AP-ATP-01.json").read_text(encoding="utf-8"))["series"]
    return next(r["record_id"] for r in reversed(rows) if r["clear"])


def test_plots_and_detail():
    plots = api.plots()["plots"]
    assert {p["plot_id"] for p in plots} == {"IN-PB-LDH-01", "IN-RJ-KTP-01", "IN-MH-LTR-01", "IN-AP-ATP-01"}
    detail = api.plot_detail("IN-AP-ATP-01")
    assert detail["series"] and detail["latest_clear"]["clear"]
    with pytest.raises(api.NotFound):
        api.plot_detail("NOPE-00")


def test_record_payload_is_self_contained():
    from fieldproof.canonical import document_hash
    from fieldproof.merkle import verify
    from fieldproof.records import leaf_fields, leaf_hash

    rec = api.record(a_record_id(), network="sepolia")  # a network with no anchor: anchor is None
    assert rec["anchor"] is None
    assert document_hash(rec["document"]) == rec["document_hash"]
    assert leaf_hash(leaf_fields(rec["document"])) == rec["leaf"]
    assert verify(rec["proof"], rec["merkle_root"], rec["leaf"])


def test_offline_verification_and_tamper():
    rid = a_record_id()
    assert api.verify(rid, offline=True)["verified"] is True
    assert api.verify(rid, offline=True, tamper="ndvi=0.9999")["verified"] is False
    with pytest.raises(api.NotFound):
        api.verify("IN-AP-ATP-01:missing", offline=True)


def test_http_server_routes_and_read_only_proxy():
    from fieldproof.server import AppServer

    server = AppServer(("127.0.0.1", 0))
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        def get(path):
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}") as r:
                return r.status, json.loads(r.read())

        assert get("/api/health")[1]["ok"] is True
        assert get("/api/plots")[0] == 200
        for path in ("/api/plots/NOPE", "/data/../.env"):
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
