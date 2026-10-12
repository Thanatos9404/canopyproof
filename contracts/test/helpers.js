// Off-chain helpers mirroring fieldproof/merkle.py and fieldproof/records.py, so the
// Solidity side is tested against exactly the same leaf and tree construction.
const { ethers } = require("hardhat");

const coder = ethers.AbiCoder.defaultAbiCoder();
const OBS_TYPES = ["bytes32", "uint64", "int32", "int32", "uint32", "int32", "int32", "int32", "uint8", "uint8", "bytes32"];
const FLAGS = { CLEAR: 1, VCI: 2, RAIN: 4, Z: 8, CROP: 16 };

function obsTuple(o) {
  return [
    o.plotKey,
    o.observedAt,
    o.ndviE4,
    o.ndmiE4,
    o.validFractionBps,
    o.vciBps,
    o.ndviZE2,
    o.rainDeparture60dBps,
    o.stressLevel,
    o.flags,
    o.documentHash,
  ];
}

function leafOf(o) {
  return ethers.keccak256(ethers.keccak256(coder.encode(OBS_TYPES, obsTuple(o))));
}

function hashPair(a, b) {
  const [x, y] = BigInt(a) <= BigInt(b) ? [a, b] : [b, a];
  return ethers.keccak256(ethers.concat([x, y]));
}

function buildTree(leaves) {
  let level = [...leaves].sort((a, b) => (BigInt(a) < BigInt(b) ? -1 : BigInt(a) > BigInt(b) ? 1 : 0));
  const levels = [level];
  while (level.length > 1) {
    const next = [];
    for (let i = 0; i < level.length; i += 2) {
      next.push(i + 1 < level.length ? hashPair(level[i], level[i + 1]) : level[i]);
    }
    level = next;
    levels.push(level);
  }
  return { root: level[0], levels };
}

function proofFor(tree, leaf) {
  let idx = tree.levels[0].indexOf(leaf);
  if (idx < 0) throw new Error("leaf not in tree");
  const proof = [];
  for (let l = 0; l < tree.levels.length - 1; l++) {
    const sibling = idx ^ 1;
    if (sibling < tree.levels[l].length) proof.push(tree.levels[l][sibling]);
    idx = Math.floor(idx / 2);
  }
  return proof;
}

let counter = 0;
function makeObs(overrides = {}) {
  counter += 1;
  return {
    plotKey: ethers.id("plot-A"),
    observedAt: 1_690_000_000 + counter * 432_000,
    ndviE4: 3120,
    ndmiE4: -450,
    validFractionBps: 9500,
    vciBps: 2100,
    ndviZE2: -150,
    rainDeparture60dBps: -4500,
    stressLevel: 3,
    flags: FLAGS.CLEAR | FLAGS.VCI | FLAGS.RAIN | FLAGS.Z | FLAGS.CROP,
    documentHash: ethers.id(`document-${counter}`),
    ...overrides,
  };
}

function batchInput(root, count, overrides = {}) {
  return {
    merkleRoot: root,
    manifestHash: ethers.id(`manifest-${root}`),
    recordCount: count,
    periodStart: 1_690_000_000,
    periodEnd: 1_700_000_000,
    uri: "data/batches/test/manifest.json",
    ...overrides,
  };
}

module.exports = { OBS_TYPES, FLAGS, obsTuple, leafOf, hashPair, buildTree, proofFor, makeObs, batchInput };
