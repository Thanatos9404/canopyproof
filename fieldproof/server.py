"""Local API + web app server (standard library only). Binds to 127.0.0.1.

* /api/...   read-only JSON API (see api.ENDPOINTS)
* /rpc/<net> JSON-RPC proxy that forwards only read methods, so the browser can query the contracts
             without CORS problems and can never send a transaction through this server
* /data/...  read-only JSON/PNG files from data/
* /          the built web app from web/dist (single-page app), when present
"""
import json
import mimetypes
import re
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import api
from .config import CONTRACTS_DIR, DATA, WEB_DIST

READ_METHODS = {
    "eth_chainId", "net_version", "eth_blockNumber", "eth_call", "eth_getCode", "eth_getLogs",
    "eth_getBlockByNumber", "eth_getBlockByHash", "eth_getTransactionByHash", "eth_getTransactionReceipt",
    "eth_getBalance", "eth_estimateGas", "eth_gasPrice", "eth_feeHistory", "eth_maxPriorityFeePerGas",
}
DATA_SUFFIXES = {".json", ".geojson", ".png"}
# Browser copy of ethers.js from the Hardhat install, so a page can work offline.
VENDOR = {"ethers.umd.min.js": CONTRACTS_DIR / "node_modules" / "ethers" / "dist" / "ethers.umd.min.js"}
LOCAL_ORIGIN = re.compile(r"^http://(localhost|127\.0\.0\.1)(:\d+)?$")
mimetypes.add_type("application/geo+json", ".geojson")
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("image/svg+xml", ".svg")


def _safe_child(base: Path, rel: str):
    base = base.resolve()
    target = (base / rel).resolve()
    if target != base and base not in target.parents:
        return None
    return target


ROUTES = [
    (re.compile(r"^/api/health$"), lambda q: {"ok": True, "version": api.__version__}),
    (re.compile(r"^/api/endpoints$"), lambda q: api.ENDPOINTS),
    (re.compile(r"^/api/state$"), lambda q: api.state()),
    (re.compile(r"^/api/plots$"), lambda q: api.plots()),
    (re.compile(r"^/api/plots/(?P<plot_id>[A-Z0-9-]+)$"), lambda q, plot_id: api.plot_detail(plot_id)),
    (re.compile(r"^/api/plots/(?P<plot_id>[A-Z0-9-]+)/notes$"),
     lambda q, plot_id: api.note(plot_id, q.get("lang", "en") if q.get("lang") in ("en", "hi") else "en")),
    (re.compile(r"^/api/batches$"), lambda q: api.batches(q.get("network"))),
    (re.compile(r"^/api/batches/(?P<name>\d{4}-\d{2})$"), lambda q, name: api.batch_detail(name, q.get("network"))),
    (re.compile(r"^/api/records/(?P<record_id>[A-Za-z0-9_:.-]+)$"),
     lambda q, record_id: api.record(record_id, q.get("network"))),
    (re.compile(r"^/api/verify/(?P<record_id>[A-Za-z0-9_:.-]+)$"),
     lambda q, record_id: api.verify(record_id, q.get("network"), tamper=q.get("tamper"),
                                     offline=q.get("offline") == "1", recompute=q.get("recompute") == "1")),
    (re.compile(r"^/api/chain$"), lambda q: api.chain_status(q.get("network"))),
    (re.compile(r"^/api/contracts$"), lambda q: api.contracts_info(q.get("network"))),
    (re.compile(r"^/api/programs$"), lambda q: api.programs(q.get("network"), q.get("plot"))),
]


class Handler(BaseHTTPRequestHandler):
    server_version = "FieldProof/0.1"

    def log_message(self, fmt, *args):
        if getattr(self.server, "verbose", False):
            super().log_message(fmt, *args)

    # -- helpers
    def _cors(self):
        origin = self.headers.get("Origin", "")
        if LOCAL_ORIGIN.match(origin):  # Vite dev server and other local front-ends only
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send(self, status, body: bytes, ctype):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self._cors()
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload, status=200):
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _file(self, path):
        if not path or not path.is_file():
            return self._json({"error": "not found"}, 404)
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/json", "application/geo+json"):
            ctype += "; charset=utf-8"
        self._send(200, path.read_bytes(), ctype)

    # -- verbs
    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        url = urlparse(self.path)
        path = unquote(url.path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        if path.startswith("/api/"):
            for pattern, fn in ROUTES:
                m = pattern.match(path)
                if m:
                    try:
                        return self._json(fn(query, **m.groupdict()))
                    except api.NotFound as exc:
                        return self._json({"error": "not found", "detail": str(exc)}, 404)
                    except Exception as exc:  # never leak stack traces to the browser
                        return self._json({"error": type(exc).__name__, "detail": str(exc)[:300]}, 500)
            return self._json({"error": "unknown endpoint", "endpoints": api.ENDPOINTS}, 404)
        if path.startswith("/vendor/"):
            return self._file(VENDOR.get(path[len("/vendor/"):]))
        if path.startswith("/data/"):
            target = _safe_child(DATA, path[len("/data/"):])
            if target is None or target.suffix not in DATA_SUFFIXES:
                return self._json({"error": "forbidden"}, 403)
            return self._file(target)
        return self._web(path)

    def _web(self, path):
        if not (WEB_DIST / "index.html").exists():
            body = ("<!doctype html><meta charset=utf-8><title>Krishyak FieldProof API</title>"
                    "<p>The API is running. Build the web app with <code>cd web &amp;&amp; npm install &amp;&amp; "
                    "npm run build</code>, or open <a href=/api/endpoints>/api/endpoints</a>.</p>").encode()
            return self._send(200, body, "text/html; charset=utf-8")
        target = _safe_child(WEB_DIST, path.lstrip("/") or "index.html")
        if target is None:
            return self._json({"error": "forbidden"}, 403)
        if not target.is_file():  # single-page app: unknown paths render index.html
            target = WEB_DIST / "index.html"
        return self._file(target)

    def do_POST(self):
        path = urlparse(self.path).path
        if not path.startswith("/rpc/"):
            return self._json({"error": "not found"}, 404)
        network = path[len("/rpc/"):]
        if network not in api.deployed_networks():
            return self._json({"error": f"network {network} not deployed"}, 404)
        from .chain import rpc_url_for

        url = rpc_url_for(network)
        length = int(self.headers.get("Content-Length", "0"))
        if length > 1_000_000:
            return self._json({"error": "too large"}, 413)
        try:
            payload = json.loads(self.rfile.read(length))
        except ValueError:
            return self._json({"error": "invalid JSON"}, 400)
        calls = payload if isinstance(payload, list) else [payload]
        if not calls or any(not isinstance(c, dict) or c.get("method") not in READ_METHODS for c in calls):
            return self._json({"jsonrpc": "2.0", "id": None,
                               "error": {"code": -32601, "message": "read-only proxy: method not allowed"}}, 403)
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read()
        except Exception as exc:
            return self._json({"jsonrpc": "2.0", "id": None,
                               "error": {"code": -32000, "message": f"upstream RPC unavailable: {type(exc).__name__}"}}, 502)
        self._send(200, body, "application/json")


class AppServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, verbose=False):
        super().__init__(addr, Handler)
        self.verbose = verbose


def serve(port=8080, verbose=False, log=print):
    server = AppServer(("127.0.0.1", port), verbose=verbose)
    log(f"Krishyak FieldProof on http://127.0.0.1:{port}  (API: /api/endpoints; Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
