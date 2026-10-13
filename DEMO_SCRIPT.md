# Demo video script (about 4 minutes)

Record at 1440 x 900, browser zoom 100%, light system theme. Before recording:

```powershell
cd contracts; npx hardhat node                       # terminal 1, leave running
.venv\Scripts\python -m canopyproof demo             # terminal 2, about 2 minutes
.venv\Scripts\python -m canopyproof serve            # terminal 2, then open http://127.0.0.1:8080
```

Keep a second terminal ready with the `verify` command from scene 6. Speak calmly; the timings below
are targets, not limits (Devpost accepts 3 to 5 minutes).

---

## 0:00-0:25 The problem (landing page, hero)

**Screen:** landing page hero, then scroll slowly to the "6%" sentence.

**Say:** "Forest carbon credits are supposed to mean a forest keeps standing. A 2023 study in *Science*
linked only about six percent of the credits expected from eighteen forest projects to real emission
cuts. And forests can burn after the credits are sold: in 2021 the Bootleg Fire burned through the
Klamath East project in Oregon. Buyers usually can't check any of this themselves: the evidence is closed."

## 0:25-0:45 What CanopyProof is

**Screen:** scroll through the three feature sections (Observe, Anchor, Enforce).

**Say:** "CanopyProof is open integrity monitoring. It reads free Sentinel-2 imagery for monitoring cells
inside a project, detects burns and clearing pixel by pixel, anchors every observation on-chain, and runs
a smart contract that gives each credit batch a public status a marketplace can check."

## 0:45-1:30 The evidence (Monitor, Klamath East fire cell)

**Screen:** click "Open the monitor", then "Monitoring cells", select "Klamath East: Bootleg Fire scar".
Toggle Baseline, Latest, Loss on the map. Hover the NDVI/NBR chart around July 2021.

**Say:** "This is a real one-kilometre cell inside Klamath East, project ACR273. Baseline view from 2019,
latest view from this month, and in terracotta the pixels where loss was confirmed. The chart shows the
vegetation index and the burn ratio; in July 2021 the burn ratio crashes below zero. One cloudy or smoky
look is never enough: loss is confirmed only after two clear looks, and smoke is filtered out because it
doesn't move the short-wave infrared. Against the independent Hansen Global Forest Change map, this cell
agrees at 99.6 percent precision and 99.9 percent recall."

## 1:30-1:50 Healthy cells, honestly

**Screen:** select "Klamath East: unburned stand", then "ACR189". Point at the confirmed-loss numbers.

**Say:** "An unburned cell in the same project and a second project stay healthy: about one hectare of
noise in seven years, below the two percent watch line."

## 1:50-2:40 The contract reacts (Credit batches)

**Screen:** "Credit batches". Show ACR273 (Reversal suspected, buffer-pool review flagged, evidence
timeline), then ACR189 (Healthy). Scroll to the GuardedCreditLedger panel.

**Say:** "Each credit batch is registered in CarbonIntegrityOracle with its vintages and monitoring cells.
Anyone can submit an anchored observation with its Merkle proof; the contract checks the proof and sets
the status from the batch's own thresholds. On the seventh of August 2021 Klamath East became 'reversal
suspected' and the contract emitted a buffer-pool review event. That status is sticky until an auditor
resolves it with a public reason. A marketplace just calls retirementCheck: here the example ledger blocks
retiring the flagged batches with IntegrityHold, and retires the healthy one."

## 2:40-3:00 No double claims (Overview)

**Screen:** "Overview", the "Same land, same year, twice?" panel.

**Say:** "In 2024 the owner tried to re-list most of the same land as new projects. Our contract enforces
the rule regulators described: the same land can't back two credit batches for the same vintage year.
Re-listing for 2019 to 2022 is refused on-chain; 2024 onwards is accepted, and the fire history stays
on the record."

## 3:00-3:45 Verify it yourself (Verify a record)

**Screen:** "Verify a record", Klamath East, the 7 August 2021 record. Click "Run the checks" and let the
steps appear. Then tick "Simulate tampering" and run again. Then click "Recompute from Sentinel-2".

**Say:** "Don't trust us. In your browser, ethers.js hashes the record, rebuilds the Merkle leaf, walks the
proof to the monthly root and asks the contract directly. Everything passes. If I change one number, the
hash and the proof fail. And recompute goes back to the original Sentinel-2 pixels and reproduces the exact
pixel digest."

(Optional, terminal, 10 seconds): run
`python -m canopyproof verify KE-FIRE-01:S2A_T10TFN_20210807T185412_L2A --recompute` and show VERIFIED.

## 3:45-4:10 Limits and what's next (Method and limits, or the landing "Evidence, not a verdict")

**Say:** "Satellite change is evidence for review, not proof of carbon loss, and it doesn't judge
additionality. This prototype runs on a local chain and is ready for Sepolia. Next: grids of cells per
project, radar for cloudy tropics, and monthly completeness proofs so no observation can be quietly left
out. CanopyProof: is the forest behind your credit still standing? Now anyone can check."

---

Checklist: show the real app above the fold within the first 10 seconds; keep the cursor slow; never
claim certified carbon numbers; mention that the Amazon cell is a representative area, not a project, if
it appears on screen.
