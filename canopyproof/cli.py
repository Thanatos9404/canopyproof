"""Command line: `python -m fieldproof <command>` (or `fieldproof <command>` after `pip install -e .`)."""
import argparse
import json
import sys
import warnings
from datetime import datetime, timezone

warnings.filterwarnings("ignore", category=UserWarning)
try:
    from rasterio.errors import NotGeoreferencedWarning

    warnings.filterwarnings("ignore", category=NotGeoreferencedWarning)
except Exception:  # rasterio is only needed for `fetch`
    pass

DEFAULT_SEASONS = [
    ("kharif-2023", "2023-06-01", "2023-10-31"),
    ("kharif-2024", "2024-06-01", "2024-10-31"),
    ("kharif-2025", "2025-06-01", "2025-10-31"),
    ("kharif-2026", "2026-06-01", "2026-10-31"),
]


def _plots(args):
    from .plots import load_plots

    plots = load_plots()
    if getattr(args, "plot", None):
        plots = [p for p in plots if p["plot_id"] in args.plot]
        if not plots:
            sys.exit(f"unknown plot(s): {args.plot}")
    return plots


def cmd_fetch(args):
    from .sentinel import fetch_plot, load_observations, make_chip
    from .weather import fetch_weather

    until = args.until or datetime.now(timezone.utc).date().isoformat()
    for plot in _plots(args):
        if not args.skip_weather:
            fetch_weather(plot, end=until)
        if not args.skip_satellite:
            fetch_plot(plot, args.since, until, workers=args.workers, history_per_month=args.history_per_month)
        _chip(plot, load_observations(plot["plot_id"]), make_chip)


def _chip(plot, obs_doc, make_chip):
    import httpx

    if not obs_doc:
        return
    clear = [o for o in obs_doc["observations"] if o["quality"]["status"] == "clear"
             and float(o["quality"]["valid_fraction"]) > 0.95 and float(o["scene_cloud_cover_pct"]) < 10]
    if not clear:
        return
    from .config import S2_COLLECTION, STAC_API

    item = httpx.get(f"{STAC_API}/collections/{S2_COLLECTION}/items/{clear[-1]['item_id']}", timeout=60).json()
    meta = make_chip(plot, item)
    print(f"  chip {plot['plot_id']} from {meta['date']}")


def cmd_chips(args):
    from .sentinel import load_observations, make_chip

    for plot in _plots(args):
        _chip(plot, load_observations(plot["plot_id"]), make_chip)


def cmd_build(args):
    from .pipeline import build_all

    build_all(_plots(args))


def cmd_deploy(args):
    from .chain import connect, deploy_local

    w3, _ = connect("localhost", require_deployment=False)
    out = deploy_local(w3)
    print(f"FieldProofRegistry       {out['contracts']['FieldProofRegistry']['address']}")
    print(f"ParametricDroughtTrigger {out['contracts']['ParametricDroughtTrigger']['address']}")
    print(f"attestor {out['roles']['attestor']}  relayer {out['accounts']['relayer']}")
    print("wrote data/chain/localhost.json")


def cmd_anchor(args):
    from .chain import anchor_all, connect

    w3, deployment = connect(args.network)
    anchor_all(w3, deployment, batches=args.batch, relayed=not args.direct)


def cmd_programs(args):
    from .chain import connect, run_drought_programs

    w3, deployment = connect(args.network)
    seasons = DEFAULT_SEASONS if not args.season else [tuple(s.split(",")) for s in args.season]
    print(f"creating prototype drought programmes on {args.network}")
    run_drought_programs(w3, deployment, _plots(args), seasons)


def cmd_verify(args):
    from .verify import format_report, to_json, verify_record

    report = verify_record(args.record_id, network=None if args.offline else args.network,
                           recompute=args.recompute, tamper=args.tamper)
    print(to_json(report) if args.json else format_report(report))
    return 0 if report.ok else 1


def cmd_records(args):
    from .config import DATA

    for plot in _plots(args):
        doc = json.loads((DATA / "series" / f"{plot['plot_id']}.json").read_text(encoding="utf-8"))
        rows = [r for r in doc["series"] if (not args.stress or r["stress"] in args.stress)
                and (not args.since or r["date"] >= args.since)]
        for r in rows[-args.limit:]:
            print(f"{r['record_id']:48s} {r['date']}  ndvi={r['ndvi'] or '-':>7}  vci={r['vci'] or '-':>5}  "
                  f"rain60={r['rain60'] or '-':>6}%  {r['stress']}")


def cmd_explain(args):
    from .config import DATA
    from .explain import explain

    for plot in _plots(args):
        doc = json.loads((DATA / "series" / f"{plot['plot_id']}.json").read_text(encoding="utf-8"))
        for lang in args.lang:
            note = explain(doc, lang=lang, use_llm=args.llm)
            if note:
                print(f"[{plot['plot_id']} {lang}] ({note['generated_by']})\n{note['text']}\n{note['disclaimer']}\n")


def cmd_serve(args):
    from .server import serve

    serve(port=args.port, verbose=args.verbose)


def cmd_demo(args):
    """Deploy to the local node, build, anchor, run drought programmes and write field notes."""
    from .chain import anchor_all, connect, deploy_local, run_drought_programs
    from .config import DATA
    from .explain import explain
    from .pipeline import build_all
    from .plots import load_plots

    plots = load_plots()
    print("1/5 building records and monthly Merkle batches from data/observations + data/weather")
    build_all(plots)
    print("2/5 deploying contracts to the local Hardhat node")
    w3, _ = connect("localhost", require_deployment=False)
    deploy_local(w3)
    w3, deployment = connect("localhost")
    print("3/5 signing and anchoring every batch")
    anchor_all(w3, deployment)
    print("4/5 prototype parametric drought programmes (kharif seasons)")
    run_drought_programs(w3, deployment, plots, DEFAULT_SEASONS)
    print("5/5 field notes (offline template; add --llm to `explain` for Nebius)")
    for plot in plots:
        doc = json.loads((DATA / "series" / f"{plot['plot_id']}.json").read_text(encoding="utf-8"))
        for lang in ("en", "hi"):
            explain(doc, lang=lang, use_llm=False)
    print("done. Next: `python -m fieldproof serve` and open http://127.0.0.1:8080 (API: /api/endpoints)")


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="fieldproof", description="Krishyak FieldProof: verifiable field climate records")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("fetch", help="download Sentinel-2 plot statistics and ERA5 weather (network)")
    p.add_argument("--plot", nargs="*")
    p.add_argument("--since", default="2019-01-01")
    p.add_argument("--until")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--history-per-month", type=int, default=3)
    p.add_argument("--skip-weather", action="store_true")
    p.add_argument("--skip-satellite", action="store_true")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("chips", help="refresh the true-colour plot images for the dashboard")
    p.add_argument("--plot", nargs="*")
    p.set_defaults(func=cmd_chips)

    p = sub.add_parser("build", help="analytics, canonical records and monthly Merkle batches (offline)")
    p.add_argument("--plot", nargs="*")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("deploy", help="deploy contracts to the local Hardhat node")
    p.set_defaults(func=cmd_deploy)

    p = sub.add_parser("anchor", help="sign and anchor batches")
    p.add_argument("--network", default="localhost", choices=["localhost", "sepolia", "amoy"])
    p.add_argument("--batch", nargs="*", help="only these months, e.g. 2026-09")
    p.add_argument("--direct", action="store_true", help="attestor sends the tx instead of a relayer")
    p.set_defaults(func=cmd_anchor)

    p = sub.add_parser("programs", help="prototype parametric drought programmes")
    p.add_argument("--network", default="localhost", choices=["localhost", "sepolia", "amoy"])
    p.add_argument("--plot", nargs="*")
    p.add_argument("--season", nargs="*", help="label,YYYY-MM-DD,YYYY-MM-DD")
    p.set_defaults(func=cmd_programs)

    p = sub.add_parser("verify", help="verify one record offline and against the chain")
    p.add_argument("record_id")
    p.add_argument("--network", default="localhost", choices=["localhost", "sepolia", "amoy"])
    p.add_argument("--offline", action="store_true", help="skip on-chain checks")
    p.add_argument("--recompute", action="store_true", help="re-read the Sentinel-2 scene and recompute")
    p.add_argument("--tamper", help="simulate an edit, e.g. ndvi=0.9000")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("records", help="list record ids")
    p.add_argument("--plot", nargs="*")
    p.add_argument("--stress", nargs="*", choices=["alert", "watch", "normal", "insufficient_data"])
    p.add_argument("--since")
    p.add_argument("--limit", type=int, default=15)
    p.set_defaults(func=cmd_records)

    p = sub.add_parser("explain", help="farmer-readable notes (offline template, or --llm via Nebius)")
    p.add_argument("--plot", nargs="*")
    p.add_argument("--lang", nargs="*", default=["en", "hi"], choices=["en", "hi"])
    p.add_argument("--llm", action="store_true")
    p.set_defaults(func=cmd_explain)

    p = sub.add_parser("serve", help="JSON API + web app on http://127.0.0.1:8080")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("demo", help="build + deploy + anchor + programmes + notes on the local node")
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
