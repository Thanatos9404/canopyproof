/**
 * In-browser verifier. Re-derives everything from the record document with ethers.js, then asks the
 * smart contracts (through the read-only /rpc proxy) instead of trusting the CanopyProof server.
 */
import type { InterfaceAbi } from "ethers";
import type { RecordPayload, VerifyStep } from "./api";
import { getJSON } from "./api";

const OBS_TYPES = ["bytes32", "bytes32", "uint64", "int32", "int32", "uint32", "uint32", "uint32", "uint32", "uint32", "uint8", "uint8", "bytes32"];
const OBS_FIELDS = [
  "siteKey",
  "projectKey",
  "observedAt",
  "ndviE4",
  "nbrE4",
  "validFractionBps",
  "disturbedBps",
  "persistentBps",
  "burnBps",
  "cumulativeLossBps",
  "status",
  "flags",
  "documentHash",
];

/** Same bytes as the Python canonicaliser: sorted keys, no whitespace, ASCII, integers only. */
export function canonical(value: unknown): string {
  if (value === null || typeof value !== "object") {
    if (typeof value === "number" && !Number.isInteger(value)) throw new Error("floats are not canonical");
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  const obj = value as Record<string, unknown>;
  return `{${Object.keys(obj)
    .sort()
    .map((k) => `${JSON.stringify(k)}:${canonical(obj[k])}`)
    .join(",")}}`;
}

/** Exact decimal string -> fixed-point integer (e.g. "0.1316" x 10^4 = 1316). */
export function fixedInt(dec: string, places = 4): number {
  const neg = dec.startsWith("-");
  const [i, f = ""] = dec.replace("-", "").split(".");
  const frac = (f + "0".repeat(places)).slice(0, places);
  if (f.length > places && /[1-9]/.test(f.slice(places))) throw new Error(`${dec} has more than ${places} decimals`);
  const v = Number(BigInt(i || "0") * 10n ** BigInt(places) + BigInt(frac || "0"));
  return neg ? -v : v;
}

type Doc = RecordPayload["document"];

export function leafFields(doc: Doc, documentHash: string): Record<string, string | number> {
  const ch = doc.change;
  const measured = ch.cumulative_loss_fraction !== null;
  const flags =
    (doc.quality.status === "clear" ? 1 : 0) |
    (measured ? 2 : 0) |
    (ch.isolation_forest?.advisory_flag ? 4 : 0) |
    (ch.in_baseline_period ? 8 : 0);
  const bps = ch.fractions_bps;
  return {
    siteKey: doc.site.site_key,
    projectKey: doc.site.project_key,
    observedAt: doc.observed_unix,
    ndviE4: doc.indices.ndvi ? fixedInt(doc.indices.ndvi.mean) : 0,
    nbrE4: doc.indices.nbr ? fixedInt(doc.indices.nbr.mean) : 0,
    validFractionBps: fixedInt(doc.quality.valid_fraction),
    disturbedBps: measured ? bps.disturbed : 0,
    persistentBps: measured ? bps.persistent : 0,
    burnBps: measured ? bps.burn : 0,
    cumulativeLossBps: measured ? bps.cumulative : 0,
    status: ch.status_code,
    flags,
    documentHash,
  };
}

export type Tamper = { field: "loss" | "ndvi"; value: string } | null;

function applyTamper(doc: Doc, tamper: Tamper): Doc {
  if (!tamper) return doc;
  const copy = JSON.parse(JSON.stringify(doc)) as Doc;
  if (tamper.field === "loss") {
    copy.change.cumulative_loss_fraction = tamper.value;
    copy.change.fractions_bps.cumulative = Math.round(Number(tamper.value) * 10_000);
  } else if (copy.indices.ndvi) {
    copy.indices.ndvi.mean = tamper.value;
  }
  return copy;
}

export async function verifyInBrowser(
  rec: RecordPayload,
  tamper: Tamper,
  onStep: (s: VerifyStep) => void,
): Promise<boolean> {
  const { ethers } = await import("ethers");
  let ok = true;
  const step = (s: VerifyStep) => {
    if (s.ok === false) ok = false;
    onStep(s);
  };
  const doc = applyTamper(rec.document, tamper);
  if (tamper) step({ step: "Tamper simulation", ok: null, detail: `local copy edited: ${tamper.field} = ${tamper.value}` });

  const dh = ethers.keccak256(ethers.toUtf8Bytes(canonical(doc)));
  step({ step: "Document hash: keccak256 of canonical JSON", ok: dh === rec.document_hash, detail: dh === rec.document_hash ? dh : `${dh}, but the leaf commits to ${rec.document_hash}` });

  const fields = leafFields(doc, dh);
  const coder = ethers.AbiCoder.defaultAbiCoder();
  const tuple = OBS_FIELDS.map((k) => fields[k]);
  const leaf = ethers.keccak256(ethers.keccak256(coder.encode(OBS_TYPES, tuple)));
  step({ step: "Leaf rebuilt from the document", ok: leaf === rec.leaf, detail: leaf });

  let node = leaf;
  for (const sib of rec.proof) {
    node = BigInt(node) <= BigInt(sib) ? ethers.keccak256(ethers.concat([node, sib])) : ethers.keccak256(ethers.concat([sib, node]));
  }
  step({ step: `Merkle proof to the ${rec.batch} batch root`, ok: node === rec.merkle_root, detail: `${rec.proof.length} sibling hashes` });

  if (!rec.network) {
    step({ step: "On-chain checks", ok: null, detail: "no deployment configured; offline checks only" });
    return ok;
  }
  try {
    const info = await getJSON<{ chainId: number; contracts: Record<string, { address: string; abi: unknown[] }> }>(
      `/api/contracts?network=${rec.network}`,
    );
    const provider = new ethers.JsonRpcProvider(`${location.origin}/rpc/${rec.network}`, ethers.Network.from(info.chainId), {
      staticNetwork: true,
      batchMaxCount: 1,
    });
    const reg = info.contracts.CanopyProofRegistry;
    const registry = new ethers.Contract(reg.address, reg.abi as InterfaceAbi, provider);
    const batchId: bigint = await registry.batchIdByRoot(rec.merkle_root);
    if (batchId === 0n) {
      step({ step: "Batch root anchored on-chain", ok: false, detail: "root not found in CanopyProofRegistry" });
      return ok;
    }
    step({ step: "Batch root anchored on-chain", ok: true, detail: `batch #${batchId} in ${fmtAddr(reg.address)} (${rec.network})` });
    const b = await registry.getBatch(batchId);
    step({ step: "On-chain manifest hash matches", ok: b.manifestHash === rec.manifest_hash, detail: b.manifestHash });
    step({ step: "Batch not revoked", ok: !b.revoked, detail: b.revoked ? "revoked" : "active" });
    const onChain: boolean = await registry.verifyObservation(batchId, tuple, rec.proof);
    step({ step: "Contract verifyObservation(batch, record, proof)", ok: onChain, detail: `returned ${onChain}, evaluated by the EVM via eth_call` });
    const sig = rec.anchor?.recordSignature;
    if (sig) {
      const signer = ethers.verifyMessage(ethers.getBytes(leaf), sig);
      const role = await registry.ATTESTOR_ROLE();
      const isAttestor: boolean = await registry.hasRole(role, signer);
      step({
        step: "EIP-191 record signature recovers to an attestor",
        ok: signer === b.attestor && isAttestor,
        detail: `signer ${fmtAddr(signer)}`,
      });
    }
  } catch (e) {
    step({ step: "On-chain checks", ok: false, detail: `chain not reachable: ${(e as Error).message.slice(0, 120)}` });
  }
  return ok;
}

const fmtAddr = (a: string) => `${a.slice(0, 8)}...${a.slice(-6)}`;
