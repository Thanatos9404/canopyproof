// Deploys CanopyProofRegistry, CarbonIntegrityOracle and GuardedCreditLedger and writes ../data/chain/<network>.json,
// the file the Python pipeline and the dashboard read.
//
//   npx hardhat run scripts/deploy.js --network localhost   (after `npx hardhat node`)
//   npx hardhat run scripts/deploy.js --network sepolia     (needs .env, see KEYS_NEEDED.md)
//
// Safety: only chain IDs in ALLOWED_CHAINS are accepted. Mainnets are refused.
const fs = require("fs");
const path = require("path");
const hre = require("hardhat");

const ALLOWED_CHAINS = { 31337: "localhost", 11155111: "sepolia", 80002: "amoy" };

async function main() {
  const { ethers, network } = hre;
  const chainId = Number((await ethers.provider.getNetwork()).chainId);
  if (!(chainId in ALLOWED_CHAINS)) {
    throw new Error(`Refusing to deploy to chainId ${chainId}. Only local and public testnets are allowed.`);
  }
  const signers = await ethers.getSigners();
  if (signers.length === 0) throw new Error("No deployer account. Set DEPLOYER_PRIVATE_KEY in the git-ignored .env.");
  const deployer = signers[0];
  const isLocal = chainId === 31337;
  // Local chain: separate well-known Hardhat dev accounts for each role (public test accounts, no value).
  // Testnet: the deployer holds every role unless ATTESTOR_ADDRESS / PROGRAM_ADMIN_ADDRESS are set.
  const attestor = isLocal ? signers[1].address : process.env.ATTESTOR_ADDRESS || deployer.address;
  const registrar = isLocal ? signers[3].address : process.env.REGISTRAR_ADDRESS || deployer.address;
  const auditor = isLocal ? signers[5].address : process.env.AUDITOR_ADDRESS || deployer.address;

  const balance = await ethers.provider.getBalance(deployer.address);
  console.log(`network=${network.name} chainId=${chainId} deployer=${deployer.address} balance=${ethers.formatEther(balance)}`);

  const Registry = await ethers.getContractFactory("CanopyProofRegistry");
  const registry = await Registry.deploy(deployer.address);
  await registry.waitForDeployment();
  const registryReceipt = await registry.deploymentTransaction().wait();

  const oracle = await (await ethers.getContractFactory("CarbonIntegrityOracle")).deploy(await registry.getAddress(), deployer.address);
  await oracle.waitForDeployment();
  const ledger = await (await ethers.getContractFactory("GuardedCreditLedger")).deploy(await oracle.getAddress(), deployer.address);
  await ledger.waitForDeployment();

  await (await registry.grantRole(await registry.ATTESTOR_ROLE(), attestor)).wait();
  await (await oracle.grantRole(await oracle.REGISTRAR_ROLE(), registrar)).wait();
  await (await oracle.grantRole(await oracle.AUDITOR_ROLE(), auditor)).wait();
  await (await ledger.grantRole(await ledger.ISSUER_ROLE(), registrar)).wait();

  const artifact = (name) => hre.artifacts.readArtifactSync(name);
  const out = {
    network: ALLOWED_CHAINS[chainId],
    chainId,
    rpcUrl: isLocal ? network.config.url || "http://127.0.0.1:8545" : null,
    explorer: { 11155111: "https://sepolia.etherscan.io", 80002: "https://amoy.polygonscan.com" }[chainId] || null,
    deployedAt: new Date().toISOString(),
    deployBlock: registryReceipt.blockNumber,
    roles: { admin: deployer.address, attestor, registrar, auditor },
    accounts: isLocal ? { relayer: signers[2].address, watchdog: signers[4].address, buyer: signers[6].address } : {},
    contracts: {
      CanopyProofRegistry: { address: await registry.getAddress(), abi: artifact("CanopyProofRegistry").abi },
      CarbonIntegrityOracle: { address: await oracle.getAddress(), abi: artifact("CarbonIntegrityOracle").abi },
      GuardedCreditLedger: { address: await ledger.getAddress(), abi: artifact("GuardedCreditLedger").abi },
    },
  };
  const dir = path.resolve(__dirname, "..", "..", "data", "chain");
  fs.mkdirSync(dir, { recursive: true });
  const file = path.join(dir, `${out.network}.json`);
  fs.writeFileSync(file, JSON.stringify(out, null, 2));
  for (const [name, c] of Object.entries(out.contracts)) console.log(`${name.padEnd(22)} ${c.address}`);
  console.log(`wrote ${path.relative(process.cwd(), file)}`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
