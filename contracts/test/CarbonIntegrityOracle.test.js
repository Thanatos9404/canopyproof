const { expect } = require("chai");
const { ethers } = require("hardhat");
const { loadFixture, time } = require("@nomicfoundation/hardhat-network-helpers");
const { FLAGS, leafOf, buildTree, proofFor, makeObs, batchInput } = require("./helpers");

const Status = { Unmonitored: 0, Healthy: 1, Watch: 2, ReversalSuspected: 3 };
const DAY = 86_400;

describe("CarbonIntegrityOracle + GuardedCreditLedger", function () {
  async function fixture() {
    const [admin, attestor, registrar, auditor, watchdog, buyer, stranger] = await ethers.getSigners();
    const registry = await (await ethers.getContractFactory("CanopyProofRegistry")).deploy(admin.address);
    await registry.grantRole(await registry.ATTESTOR_ROLE(), attestor.address);
    const oracle = await (await ethers.getContractFactory("CarbonIntegrityOracle")).deploy(await registry.getAddress(), admin.address);
    await oracle.grantRole(await oracle.REGISTRAR_ROLE(), registrar.address);
    await oracle.grantRole(await oracle.AUDITOR_ROLE(), auditor.address);
    const ledger = await (await ethers.getContractFactory("GuardedCreditLedger")).deploy(await oracle.getAddress(), admin.address);

    const now = await time.latest();
    const siteA = ethers.id("site-A");
    const siteB = ethers.id("site-B");
    const project = ethers.id("project-A");
    const t = (days) => now - 400 * DAY + days * DAY;
    const obs = {
      a1: makeObs({ siteKey: siteA, observedAt: t(10) }),
      b1: makeObs({ siteKey: siteB, observedAt: t(12) }),
      a2watch: makeObs({ siteKey: siteA, observedAt: t(40), disturbedBps: 900, burnBps: 700, cumulativeLossBps: 0, status: 2 }),
      a3reversal: makeObs({ siteKey: siteA, observedAt: t(50), disturbedBps: 9000, persistentBps: 8800, burnBps: 8500, cumulativeLossBps: 8800, status: 3 }),
      a4healthyLater: makeObs({ siteKey: siteA, observedAt: t(380), cumulativeLossBps: 100 }),
      b2recent: makeObs({ siteKey: siteB, observedAt: t(395) }),
      cloudy: makeObs({ siteKey: siteB, observedAt: t(396), flags: FLAGS.BASELINE }),
      otherProject: makeObs({ siteKey: siteA, projectKey: ethers.id("project-B"), observedAt: t(397) }),
      unknownSite: makeObs({ siteKey: ethers.id("site-Z"), observedAt: t(398) }),
    };
    const all = Object.values(obs);
    const tree = buildTree(all.map(leafOf));
    await registry.connect(attestor).anchorBatch(batchInput(tree.root, all.length));
    const proof = (o) => proofFor(tree, leafOf(o));

    const landCells = [ethers.id("cell-1"), ethers.id("cell-2")];
    const batch = {
      projectKey: project,
      registryRef: "ACR273",
      vintageStart: 2015,
      vintageEnd: 2020,
      issuedCredits: 1_140_000,
      siteKeys: [siteA, siteB],
      landCells,
      watchBps: 200,
      reversalBps: 500,
      maxEvidenceAge: 60 * DAY,
      uri: "data/projects.json",
    };
    await oracle.connect(registrar).registerCreditBatch(batch);
    return { registry, oracle, ledger, admin, registrar, auditor, watchdog, buyer, stranger, obs, proof, batch, siteA, siteB, landCells };
  }

  describe("registration and double claims", function () {
    it("registers a batch with its sites and land cells", async function () {
      const { oracle, siteA, siteB, landCells } = await loadFixture(fixture);
      expect(await oracle.creditBatchCount()).to.equal(1n);
      expect(await oracle.sitesOf(1)).to.deep.equal([siteA, siteB]);
      expect(await oracle.landCellsOf(1)).to.deep.equal(landCells);
      expect(await oracle.landClaim(landCells[0], 2017)).to.equal(1n);
      const st = await oracle.integrityStatus(1);
      expect(st.status).to.equal(Status.Unmonitored);
      expect(st.stale).to.equal(true);
      expect(await oracle.retirementCheck(1)).to.equal(1);
    });

    it("refuses the same land for an overlapping vintage, but allows later vintages", async function () {
      const { oracle, registrar, batch, landCells } = await loadFixture(fixture);
      const relist = { ...batch, registryRef: "RELIST", vintageStart: 2019, vintageEnd: 2022, siteKeys: [ethers.id("site-R")] };
      await expect(oracle.connect(registrar).registerCreditBatch(relist))
        .to.be.revertedWithCustomError(oracle, "LandAlreadyClaimed")
        .withArgs(landCells[0], 2019, 1n);
      await oracle.connect(registrar).registerCreditBatch({ ...relist, vintageStart: 2024, vintageEnd: 2026 });
      expect(await oracle.landClaim(landCells[1], 2025)).to.equal(2n);
    });

    it("validates inputs and roles", async function () {
      const { oracle, registrar, stranger, batch } = await loadFixture(fixture);
      await expect(oracle.connect(stranger).registerCreditBatch(batch)).to.be.revertedWithCustomError(
        oracle,
        "AccessControlUnauthorizedAccount"
      );
      const fresh = { ...batch, landCells: [ethers.id("cell-x")] };
      for (const bad of [
        { ...fresh, projectKey: ethers.ZeroHash },
        { ...fresh, vintageEnd: 2014 },
        { ...fresh, vintageStart: 1990, vintageEnd: 2020 },
        { ...fresh, siteKeys: [] },
        { ...fresh, watchBps: 0 },
        { ...fresh, watchBps: 600, reversalBps: 500 },
        { ...fresh, reversalBps: 10_001 },
        { ...fresh, maxEvidenceAge: 0 },
      ]) {
        await expect(oracle.connect(registrar).registerCreditBatch(bad)).to.be.revertedWithCustomError(oracle, "InvalidBatch");
      }
      await expect(oracle.connect(registrar).registerCreditBatch({ ...fresh, siteKeys: [ethers.id("s"), ethers.id("s")] }))
        .to.be.revertedWithCustomError(oracle, "DuplicateSite");
    });
  });

  describe("evidence and status", function () {
    it("moves Healthy -> Watch -> ReversalSuspected, flags the buffer pool once and stays sticky", async function () {
      const { oracle, watchdog, obs, proof, siteA } = await loadFixture(fixture);
      await oracle.connect(watchdog).submitEvidence(1, 1, obs.a1, proof(obs.a1));
      await oracle.connect(watchdog).submitEvidence(1, 1, obs.b1, proof(obs.b1));
      expect((await oracle.integrityStatus(1)).status).to.equal(Status.Healthy);

      await expect(oracle.connect(watchdog).submitEvidence(1, 1, obs.a2watch, proof(obs.a2watch)))
        .to.emit(oracle, "StatusChanged")
        .withArgs(1n, siteA, Status.Healthy, Status.Watch, obs.a2watch.observedAt);

      const tx = oracle.connect(watchdog).submitEvidence(1, 1, obs.a3reversal, proof(obs.a3reversal));
      // mean loss across the 2 cells = 4400 bps of 1.14M credits = 501,600 indicative credits
      await expect(tx).to.emit(oracle, "ReversalSuspected").withArgs(1n, siteA, obs.a3reversal.observedAt, 8800, 501_600n);
      await expect(tx).to.emit(oracle, "BufferPoolReviewFlagged").withArgs(1n, "ACR273", 501_600n, obs.a3reversal.observedAt);
      expect((await oracle.getCreditBatch(1)).bufferReviewFlagged).to.equal(true);

      // A later, calmer look does not silently clear the reversal.
      await oracle.connect(watchdog).submitEvidence(1, 1, obs.a4healthyLater, proof(obs.a4healthyLater));
      expect((await oracle.siteState(1, siteA)).status).to.equal(Status.ReversalSuspected);
      expect(await oracle.retirementCheck(1)).to.equal(4);
    });

    it("rejects stale replays, wrong projects, unknown sites, unmeasurable and unanchored evidence", async function () {
      const { oracle, watchdog, obs, proof } = await loadFixture(fixture);
      await oracle.connect(watchdog).submitEvidence(1, 1, obs.a2watch, proof(obs.a2watch));
      await expect(oracle.connect(watchdog).submitEvidence(1, 1, obs.a1, proof(obs.a1)))
        .to.be.revertedWithCustomError(oracle, "StaleEvidence")
        .withArgs(obs.a2watch.observedAt);
      await expect(oracle.connect(watchdog).submitEvidence(1, 1, obs.otherProject, proof(obs.otherProject))).to.be.revertedWithCustomError(
        oracle,
        "WrongProject"
      );
      await expect(oracle.connect(watchdog).submitEvidence(1, 1, obs.unknownSite, proof(obs.unknownSite))).to.be.revertedWithCustomError(
        oracle,
        "UnknownSite"
      );
      await expect(oracle.connect(watchdog).submitEvidence(1, 1, obs.cloudy, proof(obs.cloudy))).to.be.revertedWithCustomError(
        oracle,
        "NotMeasurable"
      );
      const forged = { ...obs.b2recent, cumulativeLossBps: 0, disturbedBps: 0, documentHash: ethers.id("forged") };
      await expect(oracle.connect(watchdog).submitEvidence(1, 1, forged, proof(obs.b2recent))).to.be.revertedWithCustomError(
        oracle,
        "InvalidProof"
      );
      await expect(oracle.submitEvidence(9, 1, obs.b2recent, proof(obs.b2recent))).to.be.revertedWithCustomError(oracle, "UnknownBatch");
    });

    it("reports stale evidence and lets an auditor resolve a cell with a public reason", async function () {
      const { oracle, auditor, stranger, watchdog, obs, proof, siteA } = await loadFixture(fixture);
      await oracle.connect(watchdog).submitEvidence(1, 1, obs.a3reversal, proof(obs.a3reversal));
      await oracle.connect(watchdog).submitEvidence(1, 1, obs.b1, proof(obs.b1));
      await expect(oracle.connect(stranger).resolveSite(1, siteA, Status.Healthy, "x")).to.be.revertedWithCustomError(
        oracle,
        "AccessControlUnauthorizedAccount"
      );
      await expect(oracle.connect(auditor).resolveSite(1, siteA, Status.Healthy, "Field visit: harvest within the project's plan"))
        .to.emit(oracle, "SiteResolved")
        .withArgs(1n, siteA, Status.ReversalSuspected, Status.Healthy, auditor.address, "Field visit: harvest within the project's plan");
      // Evidence is >60 days old for both cells -> stale.
      expect(await oracle.retirementCheck(1)).to.equal(2);
      await oracle.connect(watchdog).submitEvidence(1, 1, obs.a4healthyLater, proof(obs.a4healthyLater));
      await oracle.connect(watchdog).submitEvidence(1, 1, obs.b2recent, proof(obs.b2recent));
      expect(await oracle.retirementCheck(1)).to.equal(0);
    });
  });

  describe("GuardedCreditLedger", function () {
    it("caps minting, freezes transfers on reversal and gates retirement on a fresh healthy status", async function () {
      const { oracle, ledger, admin, buyer, watchdog, obs, proof } = await loadFixture(fixture);
      await expect(ledger.connect(admin).mint(1, admin.address, 1_140_001)).to.be.revertedWithCustomError(ledger, "ExceedsIssued");
      await ledger.connect(admin).mint(1, admin.address, 1_000);
      await expect(ledger.connect(admin).retire(1, 10, "buyer")).to.be.revertedWithCustomError(ledger, "IntegrityHold").withArgs(1);

      await oracle.connect(watchdog).submitEvidence(1, 1, obs.a4healthyLater, proof(obs.a4healthyLater));
      await oracle.connect(watchdog).submitEvidence(1, 1, obs.b2recent, proof(obs.b2recent));
      await ledger.connect(admin).transfer(1, buyer.address, 100);
      await expect(ledger.connect(buyer).retire(1, 40, "Example Co. 2026 claim")).to.emit(ledger, "Retired");
      expect(await ledger.retired(1)).to.equal(40n);
      await expect(ledger.connect(buyer).retire(1, 100, "too much")).to.be.revertedWithCustomError(ledger, "InsufficientBalance");
    });

    it("blocks transfers once a reversal is suspected", async function () {
      const { oracle, ledger, admin, buyer, watchdog, obs, proof } = await loadFixture(fixture);
      await ledger.connect(admin).mint(1, admin.address, 1_000);
      await oracle.connect(watchdog).submitEvidence(1, 1, obs.a3reversal, proof(obs.a3reversal));
      await expect(ledger.connect(admin).transfer(1, buyer.address, 1)).to.be.revertedWithCustomError(ledger, "IntegrityHold").withArgs(4);
    });
  });
});
