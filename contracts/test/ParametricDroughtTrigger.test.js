const { expect } = require("chai");
const { ethers } = require("hardhat");
const { loadFixture } = require("@nomicfoundation/hardhat-network-helpers");
const { FLAGS, leafOf, buildTree, proofFor, makeObs, batchInput } = require("./helpers");

const WINDOW_START = 1_690_000_000;
const WINDOW_END = 1_700_000_000;

describe("ParametricDroughtTrigger (prototype)", function () {
  async function fixture() {
    const [admin, attestor, programAdmin, farmer, stranger] = await ethers.getSigners();
    const registry = await (await ethers.getContractFactory("FieldProofRegistry")).deploy(admin.address);
    await registry.grantRole(await registry.ATTESTOR_ROLE(), attestor.address);
    const trigger = await (await ethers.getContractFactory("ParametricDroughtTrigger")).deploy(await registry.getAddress(), admin.address);
    await trigger.grantRole(await trigger.PROGRAM_ADMIN_ROLE(), programAdmin.address);

    const plotKey = ethers.id("plot-A");
    const obs = {
      q1: makeObs({ observedAt: WINDOW_START + 86_400 }),
      q2: makeObs({ observedAt: WINDOW_START + 10 * 86_400, vciBps: 1200, rainDeparture60dBps: -6100 }),
      q3: makeObs({ observedAt: WINDOW_START + 20 * 86_400 }),
      healthy: makeObs({ observedAt: WINDOW_START + 30 * 86_400, vciBps: 6400, ndviZE2: -40, stressLevel: 1 }),
      wetButLow: makeObs({ observedAt: WINDOW_START + 31 * 86_400, rainDeparture60dBps: 500 }),
      otherPlot: makeObs({ observedAt: WINDOW_START + 32 * 86_400, plotKey: ethers.id("plot-B") }),
      early: makeObs({ observedAt: WINDOW_START - 1 }),
      cloudy: makeObs({ observedAt: WINDOW_START + 33 * 86_400, flags: FLAGS.VCI | FLAGS.RAIN, validFractionBps: 2000 }),
      noVegetation: makeObs({ observedAt: WINDOW_START + 34 * 86_400, flags: FLAGS.CLEAR | FLAGS.RAIN }),
      noRain: makeObs({ observedAt: WINDOW_START + 35 * 86_400, flags: FLAGS.CLEAR | FLAGS.VCI | FLAGS.Z | FLAGS.CROP }),
      thin: makeObs({ observedAt: WINDOW_START + 36 * 86_400, validFractionBps: 5000 }),
      zOnly: makeObs({
        observedAt: WINDOW_START + 37 * 86_400,
        vciBps: 0,
        ndviZE2: -260,
        flags: FLAGS.CLEAR | FLAGS.RAIN | FLAGS.Z | FLAGS.CROP,
      }),
      zOffSeason: makeObs({
        observedAt: WINDOW_START + 38 * 86_400,
        vciBps: 0,
        ndviZE2: -400,
        flags: FLAGS.CLEAR | FLAGS.RAIN | FLAGS.Z,
      }),
    };
    const all = Object.values(obs);
    const tree = buildTree(all.map(leafOf));
    await registry.connect(attestor).anchorBatch(batchInput(tree.root, all.length));

    const program = {
      plotKey,
      windowStart: WINDOW_START,
      windowEnd: WINDOW_END,
      maxVciBps: 3500,
      maxNdviZE2: -200,
      maxRainDepartureBps: -2000,
      minValidFractionBps: 6000,
      requiredObservations: 2,
      terms: "Prototype: VCI < 35 and 60-day rain <= -20% on 2 acquisitions",
    };
    await trigger.connect(programAdmin).createProgram(program);
    const proof = (o) => proofFor(tree, leafOf(o));
    return { registry, trigger, admin, attestor, programAdmin, farmer, stranger, obs, tree, program, proof };
  }

  it("only program admins can create programs, and parameters are validated", async function () {
    const { trigger, stranger, programAdmin, program } = await loadFixture(fixture);
    await expect(trigger.connect(stranger).createProgram(program)).to.be.revertedWithCustomError(
      trigger,
      "AccessControlUnauthorizedAccount"
    );
    for (const bad of [
      { ...program, plotKey: ethers.ZeroHash },
      { ...program, windowEnd: program.windowStart - 1 },
      { ...program, requiredObservations: 0 },
      { ...program, minValidFractionBps: 10_001 },
    ]) {
      await expect(trigger.connect(programAdmin).createProgram(bad)).to.be.revertedWithCustomError(trigger, "InvalidProgram");
    }
    expect(await trigger.programCount()).to.equal(1n);
    await expect(trigger.getProgram(2)).to.be.revertedWithCustomError(trigger, "UnknownProgram");
  });

  it("explains why observations do not qualify", async function () {
    const { trigger, obs } = await loadFixture(fixture);
    const code = async (o) => Number(await trigger.evaluate(1, o));
    expect(await code(obs.q1)).to.equal(0);
    expect(await code(obs.otherPlot)).to.equal(1);
    expect(await code(obs.early)).to.equal(2);
    expect(await code(obs.cloudy)).to.equal(3);
    expect(await code(obs.thin)).to.equal(4);
    expect(await code(obs.noVegetation)).to.equal(5);
    expect(await code(obs.zOffSeason)).to.equal(5);
    expect(await code(obs.healthy)).to.equal(6);
    expect(await code(obs.zOnly)).to.equal(0);
    expect(await code(obs.noRain)).to.equal(7);
    expect(await code(obs.wetButLow)).to.equal(8);
  });

  it("triggers after the required number of distinct, anchored, qualifying observations", async function () {
    const { trigger, farmer, stranger, obs, proof } = await loadFixture(fixture);
    await expect(trigger.connect(farmer).submitEvidence(1, 1, obs.q1, proof(obs.q1)))
      .to.emit(trigger, "EvidenceAccepted")
      .withArgs(1n, 1n, leafOf(obs.q1), obs.q1.observedAt, obs.q1.vciBps, obs.q1.ndviZE2, obs.q1.rainDeparture60dBps, 1, farmer.address);
    expect((await trigger.getProgram(1)).triggered).to.equal(false);

    await expect(trigger.connect(farmer).submitEvidence(1, 1, obs.q1, proof(obs.q1)))
      .to.be.revertedWithCustomError(trigger, "EvidenceRejected")
      .withArgs(9);

    await expect(trigger.connect(stranger).submitEvidence(1, 1, obs.q2, proof(obs.q2))).to.emit(trigger, "DroughtTriggered");
    const p = await trigger.getProgram(1);
    expect(p.triggered).to.equal(true);
    expect(p.qualifyingCount).to.equal(2n);

    await expect(trigger.connect(farmer).submitEvidence(1, 1, obs.q3, proof(obs.q3)))
      .to.be.revertedWithCustomError(trigger, "EvidenceRejected")
      .withArgs(10);
  });

  it("accepts the seasonal z-score path when VCI is not low", async function () {
    const { trigger, farmer, obs, proof } = await loadFixture(fixture);
    await trigger.connect(farmer).submitEvidence(1, 1, obs.zOnly, proof(obs.zOnly));
    await expect(trigger.connect(farmer).submitEvidence(1, 1, obs.q2, proof(obs.q2)))
      .to.emit(trigger, "DroughtTriggered")
      .withArgs(1n, obs.q2.plotKey, (v) => v > 0n, 2);
  });

  it("rejects evidence that is not in an anchored, unrevoked batch", async function () {
    const { registry, trigger, attestor, farmer, obs, proof } = await loadFixture(fixture);
    const forged = { ...obs.q1, documentHash: ethers.id("forged document") };
    await expect(trigger.connect(farmer).submitEvidence(1, 1, forged, proof(obs.q1))).to.be.revertedWithCustomError(
      trigger,
      "InvalidProof"
    );
    const unanchored = makeObs({ observedAt: WINDOW_START + 50 * 86_400 });
    await expect(trigger.connect(farmer).submitEvidence(1, 1, unanchored, [])).to.be.revertedWithCustomError(trigger, "InvalidProof");

    await registry.connect(attestor).revokeBatch(1, "superseded");
    await expect(trigger.connect(farmer).submitEvidence(1, 1, obs.q1, proof(obs.q1))).to.be.revertedWithCustomError(
      trigger,
      "InvalidProof"
    );
  });
});
