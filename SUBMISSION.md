# Devpost submission: CanopyProof

Copy each block into the matching Devpost field. Replace the two link placeholders before submitting.

## Project name

CanopyProof

## Tagline (short description)

Is the forest behind your credit still standing? Open Sentinel-2 evidence, anchored on-chain, with a smart contract that flags reversals before credits are traded or retired.

## Track

Carbon Markets & Emissions Transparency.

How it fits the track: it is blockchain-based carbon-credit verification. Every satellite observation of a project becomes a signed, content-hashed record anchored on-chain; a smart contract derives a public integrity status per credit batch; the same contract refuses to let the same land back two credit batches for one vintage year (a double-counting guard); and an example marketplace contract freezes transfers and blocks retirement when a reversal is suspected.

## Inspiration

In 2023 a peer-reviewed study in *Science* (West et al.) found that most of 26 voluntary REDD+ forest projects did not measurably reduce deforestation, and linked only about 6% of the credits expected from 18 of them to real emission cuts (Verra disputes the method). The same year a Guardian, Die Zeit and SourceMaterial investigation called most of Verra's rainforest credits likely "phantom". Reversals after issuance happen too: the 2021 Bootleg Fire burned through Oregon's Klamath East project, and at least 1.14 million credits were cancelled from California's buffer pool. Rating agencies exist, but their evidence is closed. We wanted a buyer, a journalist or a regulator to be able to check for themselves, with open data, whether the forest is still there.

## What it does

- Monitors 1 km cells inside forest carbon projects with free Sentinel-2 imagery (no keys, no paid data), computing NDVI and the Normalized Burn Ratio pixel by pixel against each project's own baseline.
- Detects burns and clearing with transparent rules that ignore wildfire smoke, confirms loss only after two clear looks, and adds an Isolation Forest anomaly score trained only on earlier years (advisory).
- Cross-checks every cell against the independent Hansen Global Forest Change map. On the Bootleg Fire cell: precision 99.6%, recall 99.9% over 1,735 pixels.
- Turns each of 1,132 looks into a canonical, keccak256-hashed record, signs it, and anchors one Merkle root per month in `CanopyProofRegistry` (EIP-712 relayed anchoring, revocation, duplicate-root refusal).
- `CarbonIntegrityOracle` registers credit batches, refuses overlapping land claims for the same vintage, accepts Merkle-proven evidence from anyone, keeps a sticky Healthy / Watch / Reversal-suspected status, flags buffer-pool review, and answers `retirementCheck()` for marketplaces. `GuardedCreditLedger` shows a marketplace using it.
- A web app for buyers and auditors: monitoring map with Sentinel-2 and confirmed-loss overlays, time series, credit-batch integrity, and an in-browser verifier that rebuilds the leaf, walks the Merkle proof and calls the contract. A CLI can also re-read the original satellite pixels and reproduce the record's pixel digest.

Demo result: Klamath East (ACR273) is flagged "reversal suspected" on 7 Aug 2021, a month after the fire started, with 98.8 of 99.6 ha confirmed lost in the burned cell; its unburned cell and project ACR189 stay healthy; a representative Amazon frontier cell is flagged in August 2024; a re-listing of the burned land for vintages 2019-2022 is refused on-chain with `LandAlreadyClaimed`.

## How we built it

- Python pipeline: rasterio/GDAL range reads of Sentinel-2 L2A Collection 1 COGs via the Earth Search STAC API; NumPy change detection; scikit-learn Isolation Forest; canonical JSON and Merkle trees; web3.py and eth-account for signing and contract calls; a standard-library JSON API with a read-only RPC proxy.
- Solidity 0.8.28 with OpenZeppelin 5 (AccessControl, EIP712, ECDSA, MerkleProof), Hardhat 2 for tests and deployment.
- React 18, TypeScript, Vite, Tailwind CSS v4, Motion and Leaflet for the web app; ethers.js for in-browser verification.
- Open data: Copernicus Sentinel-2, Hansen Global Forest Change v1.12, CarbonPlan's CC-BY offset project boundaries.
- Tests: 20 Hardhat tests and 57 pytest tests, including an end-to-end run where the Solidity verifier accepts every Python-built leaf.

## Challenges we ran into

- Wildfire smoke. Haze over Oregon in 2021 and 2026 pushed NDVI down enough to look like loss. Requiring a change in the SWIR-based NBR as well removed those false alarms without hiding real burns.
- Fresh burn scars are often labelled "dark area" or "unclassified" by the Sentinel-2 scene classifier, so the usual "vegetation only" mask hides exactly the change we look for.
- Snow and spring phenology at 1,500 m produced false changes until the analysis was limited to each project's snow-free season.
- Making records hash identically in Python and in the browser: no floats anywhere, decimals as strings, sorted keys.
- Keeping claims honest: satellites show change, not carbon, and not additionality.

## Accomplishments that we're proud of

- A real, documented reversal (the Bootleg Fire in ACR273) detected from open data and enforced by a contract, with 99.6% precision and 99.9% recall against an independent map.
- Every number on the page can be verified by a stranger: in the browser against the contract, and by recomputing from the original satellite pixels.
- The double-claim guard turns the 2024 re-listing debate ("fine as long as trees aren't double counted") into a rule a contract enforces.

## What we learned

The hard part of carbon integrity is not storing data on a chain; it is producing evidence that is open, deterministic and humble about its limits. Small details (scene classes, smoke, snow, persistence) decide whether a dashboard is trustworthy.

## What's next

Systematic grids of cells per project; Sentinel-1 radar for cloudy tropics; a monthly completeness proof against the public STAC catalogue so an attestor cannot quietly omit inconvenient looks; multiple independent attestors; adapters for registry boundaries and issuance data; IPFS storage for batch files; a pilot with a registry or a ratings team on testnet.

## Target users, scale and adoption

Credit buyers and their auditors, registries and buffer-pool managers, marketplaces, journalists and NGOs acting as watchdogs. Adoption is light: a marketplace adds one `retirementCheck()` call; a registry registers a batch with its monitoring cells; anyone can submit evidence. Costs scale with cells, not hectares: Sentinel-2 is free, one monthly root costs about 230,000 gas, and the pipeline runs on a laptop.

## Built with

python, solidity, openzeppelin, hardhat, web3.py, ethers.js, react, typescript, vite, tailwindcss, leaflet, rasterio, gdal, numpy, scikit-learn, sentinel-2, copernicus, stac, hansen-global-forest-change, ethereum, merkle-tree, eip-712

## Links

- Code: `https://github.com/Thanatos9404/canopyproof` (create and make public before submitting)
- Demo video: `<YouTube or Vimeo link>`
- Prototype: local demo (README "Quick start"); screenshots in `docs/screenshots/`

## Honest limits (paste into the description or the README link)

Prototype on a local chain, ready for Sepolia/Amoy; no mainnet and no real credits. Satellite change detection is evidence for review, not proof of carbon loss. It checks permanence, not additionality or baselines. Four 1 km cells are an illustration, not a validation study; the Amazon cell is a representative forest area, not a carbon project, and issued-credit numbers other than ACR273's documented 1.14 million are illustrative.
