require("@nomicfoundation/hardhat-chai-matchers");
require("@nomicfoundation/hardhat-ethers");
require("@nomicfoundation/hardhat-verify");
const path = require("path");

// Secrets live only in the repository-root .env (git-ignored). Nothing here is required for the
// local chain; testnet networks are only usable once KEYS_NEEDED.md steps are done.
require("dotenv").config({ path: path.resolve(__dirname, "..", ".env") });

const { SEPOLIA_RPC_URL, AMOY_RPC_URL, DEPLOYER_PRIVATE_KEY, ETHERSCAN_API_KEY, LOCAL_RPC_URL } = process.env;
const testnetAccounts = DEPLOYER_PRIVATE_KEY ? [DEPLOYER_PRIVATE_KEY] : [];

/** @type import('hardhat/config').HardhatUserConfig */
module.exports = {
  solidity: {
    version: "0.8.28",
    settings: { optimizer: { enabled: true, runs: 200 }, evmVersion: "cancun" },
  },
  networks: {
    hardhat: { chainId: 31337 },
    localhost: { url: LOCAL_RPC_URL || "http://127.0.0.1:8545", chainId: 31337 },
    // Public testnets only. Mainnets are deliberately absent and refused by scripts/deploy.js.
    sepolia: {
      url: SEPOLIA_RPC_URL || "https://ethereum-sepolia-rpc.publicnode.com",
      chainId: 11155111,
      accounts: testnetAccounts,
    },
    amoy: {
      url: AMOY_RPC_URL || "https://rpc-amoy.polygon.technology",
      chainId: 80002,
      accounts: testnetAccounts,
    },
  },
  etherscan: { apiKey: ETHERSCAN_API_KEY || "" },
  sourcify: { enabled: false },
};
