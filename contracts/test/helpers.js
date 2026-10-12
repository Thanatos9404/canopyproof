// Off-chain helpers mirroring canopyproof/merkle.py and fieldproof/records.py, so the
// Solidity side is tested against exactly the same leaf and tree construction.
const { ethers } = require("hardhat");

const coder = ethers.AbiCoder.defaultAbiCoder();
const OBS_TYPES = ["bytes32", "bytes32", "uint64", "int32", "int32", "uint32", "uint32", "uint32", "uint32", "uint32", "uint8", "uint8", "bytes32"];
const FLAGS = { CLEAR: 1, BASELINE: 2, ML: 4, BASELINE_PERIOD: 8 };

function obsTuple(o) {
  return [
    o.siteKey,
    o.projectKey,
    o.observedAt,
    o.ndviE4,
    o.nbrE4,
    o.validFractionBps,
    o.disturbedBps,
    o.persistentBps,
    o.burnBps,
    o.cumulativeLossBps,
    o.status,
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
    siteKey: ethers.id("site-A"),
    projectKey: ethers.id("project-A"),
    observedAt: 1_690_000_000 + counter * 432_000,
    ndviE4: 7800,
    nbrE4: 5200,
    validFractionBps: 9500,
    disturbedBps: 50,
    persistentBps: 0,
    burnBps: 0,
    cumulativeLossBps: 0,
    status: 1,
    flags: FLAGS.CLEAR | FLAGS.BASELINE,
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
