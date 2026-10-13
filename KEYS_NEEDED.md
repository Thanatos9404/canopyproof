# Keys and accounts

**Nothing here is needed for the local demo.** The local Hardhat node, Sentinel-2 (Earth Search), Hansen
GFC and the committed data all work without any key or account. Everything below is optional and must
be created by the project owner, never by a script or an assistant.

All secrets go only in the repository-root `.env`, which is git-ignored. Copy `.env.example` to `.env`
and fill in what you need. Never commit `.env`, never paste a key into an issue or a chat, and never use
a wallet that holds real money.

## 1. Public testnet deployment (optional)

Pick one testnet. The deploy script refuses every chain except Hardhat (31337), Sepolia (11155111) and
Polygon Amoy (80002).

| What | Where | Put in `.env` |
|---|---|---|
| A **new, empty** wallet used only for testnets (MetaMask: "Add account", then "Account details" > "Show private key") | MetaMask or any EVM wallet | `DEPLOYER_PRIVATE_KEY=0x...` |
| Testnet ETH or POL from a faucet | Sepolia: e.g. Google Cloud Web3 faucet or the Alchemy Sepolia faucet. Amoy: the Polygon faucet. Some faucets need a small mainnet balance or a login, which is your call. | nothing |
| RPC URL (optional; public defaults are built in) | Public: `https://ethereum-sepolia-rpc.publicnode.com`, `https://rpc-amoy.polygon.technology`. Or a free Alchemy/Infura key. | `SEPOLIA_RPC_URL=` / `AMOY_RPC_URL=` |
| Explorer API key (optional, only for source verification) | etherscan.io account | `ETHERSCAN_API_KEY=` |

Budget: anchoring one monthly root costs about 230,000 gas; all 82 roots are about 19 million gas,
registering the three credit batches about 3.2 million, and the watchdog evidence about 40 transactions.
At 2 gwei that is roughly 0.05 test ETH in total. Anchor only some months (`--batch`) if the faucet is stingy.

Then:

```powershell
cd contracts
npx hardhat run scripts/deploy.js --network sepolia          # writes data/chain/sepolia.json
cd ..
.venv\Scripts\python -m canopyproof anchor --network sepolia --direct
.venv\Scripts\python -m canopyproof integrity --network sepolia
.venv\Scripts\python -m canopyproof verify KE-FIRE-01:S2A_T10TFN_20210807T185412_L2A --network sepolia
```

Optional: `npx hardhat verify --network sepolia <address> <constructor args>` to publish the source on
the explorer, then add the contract addresses and explorer links to the README and the Devpost page.

On a testnet one key plays attestor, registrar and auditor unless you set `ATTESTOR_ADDRESS`,
`REGISTRAR_ADDRESS` and `AUDITOR_ADDRESS` (with `ATTESTOR_PRIVATE_KEY` for a separate attestor).

## 2. Plain-language notes with an LLM (optional)

`python -m canopyproof notes --llm` rephrases the facts of each cell with an open-weight model on Nebius
AI Studio (Token Factory). Without a key it uses the built-in template.

| What | Put in `.env` |
|---|---|
| Nebius Token Factory API key | `NEBIUS_API_KEY=...` or `NEBIUS_ENV_FILE=<path to another .env that has it>` |
| Model (optional) | `CANOPYPROOF_LLM_MODEL=Qwen/Qwen3-30B-A3B-Instruct-2507` |

## 3. Publishing (your decision)

- Create the public GitHub repository (suggested name `canopyproof`) and push. The footer links to
  `https://github.com/Thanatos9404/canopyproof`; change `GITHUB` in `web/src/landing/Landing.tsx` if you pick another name.
- Optional static hosting of `web/dist` needs the Python API for live data; the simplest public demo is
  the video plus the repository.
