const { expect } = require("chai");
const { ethers } = require("hardhat");
const { loadFixture } = require("@nomicfoundation/hardhat-network-helpers");
const { leafOf, buildTree, proofFor, makeObs, batchInput } = require("./helpers");

describe("FieldProofRegistry", function () {
  async function deployFixture() {
    const [admin, attestor, relayer, stranger] = await ethers.getSigners();
    const Registry = await ethers.getContractFactory("FieldProofRegistry");
    const registry = await Registry.deploy(admin.address);
    await registry.grantRole(await registry.ATTESTOR_ROLE(), attestor.address);

    const observations = Array.from({ length: 7 }, (_, i) => makeObs({ ndviE4: 2000 + i * 100 }));
    const tree = buildTree(observations.map(leafOf));
    return { registry, admin, attestor, relayer, stranger, observations, tree };
  }

  async function signBatch(registry, signer, input, nonce) {
    const { chainId } = await ethers.provider.getNetwork();
    const domain = {
      name: "FieldProofRegistry",
      version: "1",
      chainId,
      verifyingContract: await registry.getAddress(),
    };
    const types = {
      Batch: [
        { name: "merkleRoot", type: "bytes32" },
        { name: "manifestHash", type: "bytes32" },
        { name: "recordCount", type: "uint32" },
        { name: "periodStart", type: "uint64" },
        { name: "periodEnd", type: "uint64" },
        { name: "uri", type: "string" },
        { name: "nonce", type: "uint256" },
      ],
    };
    const value = { ...input, nonce };
    return { signature: await signer.signTypedData(domain, types, value), digest: ethers.TypedDataEncoder.hash(domain, types, value) };
  }

  describe("deployment", function () {
    it("grants admin and attestor roles", async function () {
      const { registry, admin, attestor, stranger } = await loadFixture(deployFixture);
      expect(await registry.hasRole(await registry.DEFAULT_ADMIN_ROLE(), admin.address)).to.equal(true);
      expect(await registry.hasRole(await registry.ATTESTOR_ROLE(), attestor.address)).to.equal(true);
      expect(await registry.hasRole(await registry.ATTESTOR_ROLE(), stranger.address)).to.equal(false);
      expect(await registry.batchCount()).to.equal(0n);
    });

    it("rejects a zero admin", async function () {
      const Registry = await ethers.getContractFactory("FieldProofRegistry");
      await expect(Registry.deploy(ethers.ZeroAddress)).to.be.revertedWithCustomError(Registry, "ZeroAddress");
    });
  });

  describe("anchoring", function () {
    it("anchors a batch and stores its metadata", async function () {
      const { registry, attestor, tree } = await loadFixture(deployFixture);
      const input = batchInput(tree.root, 7);
      await expect(registry.connect(attestor).anchorBatch(input))
        .to.emit(registry, "BatchAnchored")
        .withArgs(1n, tree.root, attestor.address, input.manifestHash, 7, input.periodStart, input.periodEnd, input.uri, attestor.address);
      const batch = await registry.getBatch(1);
      expect(batch.merkleRoot).to.equal(tree.root);
      expect(batch.attestor).to.equal(attestor.address);
      expect(batch.recordCount).to.equal(7n);
      expect(batch.revoked).to.equal(false);
      expect(batch.uri).to.equal(input.uri);
      expect(await registry.batchIdByRoot(tree.root)).to.equal(1n);
      expect(await registry.batchCount()).to.equal(1n);
    });

    it("only lets attestors anchor directly", async function () {
      const { registry, stranger, tree } = await loadFixture(deployFixture);
      await expect(registry.connect(stranger).anchorBatch(batchInput(tree.root, 7))).to.be.revertedWithCustomError(
        registry,
        "AccessControlUnauthorizedAccount"
      );
    });

    it("rejects empty roots, empty batches, inverted periods and duplicate roots", async function () {
      const { registry, attestor, tree } = await loadFixture(deployFixture);
      const r = registry.connect(attestor);
      await expect(r.anchorBatch(batchInput(ethers.ZeroHash, 1))).to.be.revertedWithCustomError(registry, "EmptyRoot");
      await expect(r.anchorBatch(batchInput(tree.root, 0))).to.be.revertedWithCustomError(registry, "EmptyBatch");
      await expect(r.anchorBatch(batchInput(tree.root, 7, { periodStart: 10, periodEnd: 9 }))).to.be.revertedWithCustomError(
        registry,
        "InvalidPeriod"
      );
      await r.anchorBatch(batchInput(tree.root, 7));
      await expect(r.anchorBatch(batchInput(tree.root, 7)))
        .to.be.revertedWithCustomError(registry, "RootAlreadyAnchored")
        .withArgs(1n);
    });

    it("rejects unknown batch ids in getBatch", async function () {
      const { registry } = await loadFixture(deployFixture);
      await expect(registry.getBatch(1)).to.be.revertedWithCustomError(registry, "UnknownBatch").withArgs(1n);
    });
  });

  describe("verification", function () {
    it("verifies every record of an odd-sized batch and matches the Solidity leaf", async function () {
      const { registry, attestor, observations, tree } = await loadFixture(deployFixture);
      await registry.connect(attestor).anchorBatch(batchInput(tree.root, 7));
      for (const o of observations) {
        expect(await registry.observationLeaf(o)).to.equal(leafOf(o));
        expect(await registry.verifyObservation(1, o, proofFor(tree, leafOf(o)))).to.equal(true);
      }
    });

    it("fails for a tampered value, a wrong batch, or an unknown batch", async function () {
      const { registry, attestor, observations, tree } = await loadFixture(deployFixture);
      await registry.connect(attestor).anchorBatch(batchInput(tree.root, 7));
      const o = observations[3];
      const proof = proofFor(tree, leafOf(o));
      expect(await registry.verifyObservation(1, { ...o, ndviE4: o.ndviE4 + 1 }, proof)).to.equal(false);
      expect(await registry.verifyObservation(1, { ...o, documentHash: ethers.id("forged") }, proof)).to.equal(false);
      expect(await registry.verifyObservation(0, o, proof)).to.equal(false);
      expect(await registry.verifyObservation(2, o, proof)).to.equal(false);
    });

    it("verifies a single-record batch with an empty proof", async function () {
      const { registry, attestor } = await loadFixture(deployFixture);
      const o = makeObs();
      await registry.connect(attestor).anchorBatch(batchInput(leafOf(o), 1));
      expect(await registry.verifyObservation(1, o, [])).to.equal(true);
    });
  });

  describe("revocation", function () {
    it("lets the attestor or admin revoke, and revoked batches stop verifying", async function () {
      const { registry, admin, attestor, stranger, observations, tree } = await loadFixture(deployFixture);
      await registry.connect(attestor).anchorBatch(batchInput(tree.root, 7));
      const o = observations[0];
      const proof = proofFor(tree, leafOf(o));
      await expect(registry.connect(stranger).revokeBatch(1, "nope")).to.be.revertedWithCustomError(registry, "NotAuthorized");
      await expect(registry.connect(attestor).revokeBatch(1, "processing bug"))
        .to.emit(registry, "BatchRevoked")
        .withArgs(1n, attestor.address, "processing bug");
      expect(await registry.verifyObservation(1, o, proof)).to.equal(false);
      await expect(registry.connect(admin).revokeBatch(1, "again")).to.be.revertedWithCustomError(registry, "AlreadyRevoked");
    });
  });

  describe("relayed anchoring (EIP-712)", function () {
    it("accepts an attestor signature submitted by a relayer and increments the nonce", async function () {
      const { registry, attestor, relayer, tree } = await loadFixture(deployFixture);
      const input = batchInput(tree.root, 7);
      const { signature, digest } = await signBatch(registry, attestor, input, 0);
      expect(await registry.batchDigest(input, 0)).to.equal(digest);
      await expect(registry.connect(relayer).anchorBatchWithSignature(input, attestor.address, signature))
        .to.emit(registry, "BatchAnchored")
        .withArgs(1n, tree.root, attestor.address, input.manifestHash, 7, input.periodStart, input.periodEnd, input.uri, relayer.address);
      expect(await registry.nonces(attestor.address)).to.equal(1n);
      expect((await registry.getBatch(1)).attestor).to.equal(attestor.address);
    });

    it("rejects replays, non-attestor signers and mismatched attestors", async function () {
      const { registry, attestor, relayer, stranger, tree } = await loadFixture(deployFixture);
      const input = batchInput(tree.root, 7);
      const { signature } = await signBatch(registry, attestor, input, 0);
      await registry.connect(relayer).anchorBatchWithSignature(input, attestor.address, signature);
      await expect(registry.connect(relayer).anchorBatchWithSignature(input, attestor.address, signature)).to.be.revertedWithCustomError(
        registry,
        "InvalidSignature"
      );

      const other = batchInput(ethers.id("another root"), 3);
      const forged = await signBatch(registry, stranger, other, 0);
      await expect(registry.connect(relayer).anchorBatchWithSignature(other, stranger.address, forged.signature)).to.be.revertedWithCustomError(
        registry,
        "InvalidSignature"
      );
      await expect(registry.connect(relayer).anchorBatchWithSignature(other, attestor.address, forged.signature)).to.be.revertedWithCustomError(
        registry,
        "InvalidSignature"
      );
    });
  });
});
