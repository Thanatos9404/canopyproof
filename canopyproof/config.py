"""Paths and constants shared by the pipeline."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SITES_FILE = DATA / "sites.geojson"
PROJECTS_FILE = DATA / "projects.json"
OBSERVATIONS_DIR = DATA / "observations"
RASTERS_DIR = DATA / "rasters"
BATCHES_DIR = DATA / "batches"
SERIES_DIR = DATA / "series"
VALIDATION_DIR = DATA / "validation"
CHAIN_DIR = DATA / "chain"
CHIPS_DIR = DATA / "chips"
NOTES_DIR = DATA / "notes"
INTEGRITY_DIR = DATA / "integrity"
CONTRACTS_DIR = ROOT / "contracts"
ARTIFACTS_DIR = CONTRACTS_DIR / "artifacts" / "contracts"
WEB_DIST = ROOT / "web" / "dist"

# Earth Search (Element 84) STAC API: free, no key. Sentinel-2 Collection 1 Level-2A COGs on AWS.
STAC_API = "https://earth-search.aws.element84.com/v1"
S2_COLLECTION = "sentinel-2-c1-l2a"

# Hansen / UMD / Google / USGS / NASA Global Forest Change (CC BY 4.0), used as an independent check.
HANSEN_BASE = "https://storage.googleapis.com/earthenginepartners-hansen/GFC-2024-v1.12"

# Only these chains may ever be used. Mainnets are refused everywhere.
ALLOWED_CHAINS = {31337: "localhost", 11155111: "sepolia", 80002: "amoy"}
