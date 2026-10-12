"""Command line: `python -m canopyproof <command>`."""
import argparse
import json
import sys
import warnings
from datetime import datetime, timezone

warnings.filterwarnings("ignore", category=UserWarning)
try:
    from rasterio.errors import NotGeoreferencedWarning

    warnings.filterwarnings("ignore", category=NotGeoreferencedWarning)
except Exception:  # rasterio is only needed for fetch/validate
    pass


def _sites(args):
    from .sites import load_sites

    sites = load_sites()
    if getattr(args, "site", None):
        sites = [s for s in sites if s["site_id"] in args.site]
        if not sites:
            sys.exit(f"unknown site(s): {args.site}")
    return sites


def _projects():
    from .sites import load_projects

    return load_projects()


def _relisting():
    from .config import PROJECTS_FILE

    return json.loads(PROJECTS_FILE.read_text(encoding="utf-8"))["relisting_demo"]


def cmd_fetch(args):
    from .sentinel import fetch_site

    until = args.until or datetime.now(timezone.utc).date().isoformat()
    for site in _sites(args):
        fetch_site(site, args.since, until, workers=args.workers, history_per_month=args.history_per_month)
        _chip(site)


def _chip(site):
    import httpx

    from .config import S2_COLLECTION, STAC_API
    from .sentinel import load_observations, make_chip

    doc = load_observations(site["site_id"])
    if not doc:
        return
    clear = [o for o in doc["observations"] if o["quality"]["status"] == "clear"
             and float(o["quality"]["valid_fraction"]) > 0.97 and float(o["scene_cloud_cover_pct"]) < 10]
    months = next(p["credit_batch"].get("monitoring_months") for p in _projects() if p["project_id"] == site["project_id"])
    clear = [o for o in clear if not months or int(o["date"][5:7]) in months] or clear
    if not clear:
        return
    for name, obs in ((site["site_id"], clear[-1]), (site["site_id"] + ".first", clear[0])):
        item = httpx.get(f"{STAC_API}/collections/{S2_COLLECTION}/items/{obs['item_id']}", timeout=60).json()
        meta = make_chip(site, item, name=name)
        print(f"  chip {name} from {meta['date']}")


def cmd_chips(args):
    for site in _sites(args):
        _chip(site)


def cmd_build(args):
    from .pipeline import build_all

    build_all(_sites(args), _projects())


def cmd_validate(args):
    from .hansen import validate_site
    from .pipeline import site_documents

    projects = {p["project_id"]: p for p in _projects()}
    for site in _sites(args):
        docs, summary, obs_doc = site_documents(site, projects[site["project_id"]])
        if docs:
            validate_site(site, obs_doc, summary)


def cmd_deploy(args):
    from .chain import connect, deploy_local

    w3, _ = connect("localhost", require_deployment=False)
    out = deploy_local(w3)
    for name, c in out["contracts"].items():
        print(f"{name:22s} {c['address']}")
    print("wrote data/chain/localhost.json")


def cmd_anchor(args):
    from .chain import anchor_all, connect

    w3, deployment = connect(args.network)
    anchor_all(w3, deployment, batches=args.batch, relayed=not args.direct)


def cmd_integrity(args):
    from .chain import connect, run_integrity
    from .sites import load_sites

    w3, deployment = connect(args.network)
    run_integrity(w3, deployment, _projects(), _relisting(), load_sites())


def cmd_verify(args):
    from .verify import format_report, to_json, verify_record

    report = verify_record(args.record_id, network=None if args.offline else args.network,
                           recompute=args.recompute, tamper=args.tamper)
    print(to_json(report) if args.json else format_report(report))
    return 0 if report.ok else 1


def cmd_records(args):
    from .config import SERIES_DIR

    for site in _sites(args):
        doc = json.loads((SERIES_DIR / f"{site['site_id']}.json").read_text(encoding="utf-8"))
        rows = [r for r in doc["series"] if (not args.status or r["status"] in args.status)
                and (not args.since or r["date"] >= args.since)]
        for r in rows[-args.limit:]:
            print(f"{r['record_id']:50s} {r['date']}  ndvi={r['ndvi'] or '-':>7}  nbr={r['nbr'] or '-':>7}  "
                  f"loss={r['loss'] or '-':>6}  {r['status']}")


def cmd_notes(args):
    from .config import SERIES_DIR
    from .explain import explain

    for site in _sites(args):
        doc = json.loads((SERIES_DIR / f"{site['site_id']}.json").read_text(encoding="utf-8"))
        note = explain(doc, use_llm=args.llm)
        if note:
            print(f"[{site['site_id']}] ({note['generated_by']})\n{note['text']}\n{note['disclaimer']}\n")


def cmd_serve(args):
    from .server import serve

    serve(port=args.port, verbose=args.verbose)


def cmd_demo(args):
    """Build, deploy to the local node, anchor, run the integrity oracle and write notes."""
    from .chain import anchor_all, connect, deploy_local, run_integrity
    from .config import SERIES_DIR
    from .explain import explain
    from .pipeline import build_all
    from .sites import load_sites

    sites, projects = load_sites(), _projects()
    print("1/5 change analytics, canonical records and monthly Merkle batches")
    build_all(sites, projects)
    print("2/5 deploying contracts to the local Hardhat node")
    w3, _ = connect("localhost", require_deployment=False)
    deploy_local(w3)
    w3, deployment = connect("localhost")
    print("3/5 signing and anchoring every batch")
    anchor_all(w3, deployment)
    print("4/5 credit batches, double-claim check, watchdog evidence, guarded ledger")
    run_integrity(w3, deployment, projects, _relisting(), sites)
    print("5/5 integrity notes (offline template; `notes --llm` uses Nebius)")
    for site in sites:
        path = SERIES_DIR / f"{site['site_id']}.json"
        if path.exists():
            explain(json.loads(path.read_text(encoding="utf-8")))
    print("done. Next: `python -m canopyproof serve` and open http://127.0.0.1:8080")


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="canopyproof",
                                     description="CanopyProof: open, satellite-verified integrity monitoring for forest carbon credits")
    sub = parser.add_subparsers(dest="command", required=True)
    nets = ["localhost", "sepolia", "amoy"]

    p = sub.add_parser("fetch", help="read Sentinel-2 cell rasters from Earth Search (network)")
    p.add_argument("--site", nargs="*")
    p.add_argument("--since", default="2019-01-01")
    p.add_argument("--until")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--history-per-month", type=int, default=3)
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("chips", help="refresh the true-colour cell images (network)")
    p.add_argument("--site", nargs="*")
    p.set_defaults(func=cmd_chips)

    p = sub.add_parser("build", help="change analytics, canonical records and monthly Merkle batches (offline)")
    p.add_argument("--site", nargs="*")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("validate", help="compare confirmed loss with Hansen Global Forest Change (network)")
    p.add_argument("--site", nargs="*")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("deploy", help="deploy contracts to the local Hardhat node")
    p.set_defaults(func=cmd_deploy)

    p = sub.add_parser("anchor", help="sign and anchor batches")
    p.add_argument("--network", default="localhost", choices=nets)
    p.add_argument("--batch", nargs="*", help="only these months, e.g. 2026-09")
    p.add_argument("--direct", action="store_true", help="attestor sends the tx instead of a relayer")
    p.set_defaults(func=cmd_anchor)

    p = sub.add_parser("integrity", help="register credit batches, submit evidence, run the guarded ledger demo")
    p.add_argument("--network", default="localhost", choices=nets)
    p.set_defaults(func=cmd_integrity)

    p = sub.add_parser("verify", help="verify one record offline and against the chain")
    p.add_argument("record_id")
    p.add_argument("--network", default="localhost", choices=nets)
    p.add_argument("--offline", action="store_true", help="skip on-chain checks")
    p.add_argument("--recompute", action="store_true", help="re-read the Sentinel-2 scene and recompute")
    p.add_argument("--tamper", help="simulate an edit, e.g. loss=0.0000 or ndvi=0.9000")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("records", help="list record ids")
    p.add_argument("--site", nargs="*")
    p.add_argument("--status", nargs="*", choices=["reversal_suspected", "watch", "healthy", "insufficient_data"])
    p.add_argument("--since")
    p.add_argument("--limit", type=int, default=15)
    p.set_defaults(func=cmd_records)

    p = sub.add_parser("notes", help="plain-language integrity notes (offline template, or --llm via Nebius)")
    p.add_argument("--site", nargs="*")
    p.add_argument("--llm", action="store_true")
    p.set_defaults(func=cmd_notes)

    p = sub.add_parser("serve", help="JSON API + web app on http://127.0.0.1:8080")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("demo", help="build + deploy + anchor + integrity + notes on the local node")
    p.set_defaults(func=cmd_demo)

    args = parser.parse_args(argv)
    try:
        return args.func(args) or 0
    except Exception as exc:
        from .chain import ChainError

        if isinstance(exc, ChainError):
            print(f"error: {exc}", file=sys.stderr)
            return 2
        raise
