"""Paths and constants shared by the pipeline."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
PLOTS_FILE = DATA / "plots.geojson"
OBSERVATIONS_DIR = DATA / "observations"
WEATHER_DIR = DATA / "weather"
ANALYSIS_DIR = DATA / "analysis"
BATCHES_DIR = DATA / "batches"
CHAIN_DIR = DATA / "chain"
CHIPS_DIR = DATA / "chips"
EXPLANATIONS_DIR = DATA / "explanations"
PROGRAMS_DIR = DATA / "programs"
SERIES_DIR = DATA / "series"
WEB_DIST = ROOT / "web" / "dist"
CONTRACTS_DIR = ROOT / "contracts"
ARTIFACTS_DIR = CONTRACTS_DIR / "artifacts" / "contracts"

# Earth Search (Element 84) STAC API: free, no key. Sentinel-2 Collection 1 Level-2A COGs on AWS.
STAC_API = "https://earth-search.aws.element84.com/v1"
S2_COLLECTION = "sentinel-2-c1-l2a"

# Open-Meteo historical weather API (ERA5 reanalysis): free for non-commercial use, no key.
OPEN_METEO_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
CLIMATE_NORMAL_YEARS = (1991, 2020)

# Only these chains may ever be used. Mainnets are refused everywhere.
ALLOWED_CHAINS = {31337: "localhost", 11155111: "sepolia", 80002: "amoy"}
