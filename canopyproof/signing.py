"""Attestor signatures: EIP-191 over each record leaf, EIP-712 over each batch.

Two signer back-ends, neither of which ever writes a key to disk:
  * RpcSigner   - the local Hardhat node signs with its built-in, publicly known dev accounts.
  * EnvKeySigner - for public testnets, the key is read at runtime from the git-ignored .env
                   (ATTESTOR_PRIVATE_KEY, falling back to DEPLOYER_PRIVATE_KEY). See KEYS_NEEDED.md.
"""
import json
import os

from eth_account import Account
from eth_account.messages import encode_defunct, encode_typed_data

BATCH_TYPES = [
    {"name": "merkleRoot", "type": "bytes32"},
    {"name": "manifestHash", "type": "bytes32"},
    {"name": "recordCount", "type": "uint32"},
    {"name": "periodStart", "type": "uint64"},
    {"name": "periodEnd", "type": "uint64"},
    {"name": "uri", "type": "string"},
    {"name": "nonce", "type": "uint256"},
]
DOMAIN_TYPES = [
    {"name": "name", "type": "string"},
    {"name": "version", "type": "string"},
    {"name": "chainId", "type": "uint256"},
    {"name": "verifyingContract", "type": "address"},
]


def batch_typed_data(chain_id, registry, batch_input, nonce):
    return {
        "types": {"EIP712Domain": DOMAIN_TYPES, "Batch": BATCH_TYPES},
        "primaryType": "Batch",
        "domain": {"name": "FieldProofRegistry", "version": "1", "chainId": chain_id, "verifyingContract": registry},
        "message": {**batch_input, "nonce": nonce},
    }


def _bytes32(h):
    return bytes.fromhex(h[2:] if h.startswith("0x") else h)


def recover_leaf_signer(leaf_hex, signature):
    return Account.recover_message(encode_defunct(primitive=_bytes32(leaf_hex)), signature=signature)


def recover_typed_signer(typed, signature):
    return Account.recover_message(encode_typed_data(full_message=typed), signature=signature)


class RpcSigner:
    """Signs through a local development node's unlocked accounts (Hardhat)."""

    kind = "rpc"

    def __init__(self, w3, address):
        self.w3 = w3
        self.address = w3.to_checksum_address(address)

    def _request(self, method, params):
        resp = self.w3.provider.make_request(method, params)
        if "error" in resp:
            raise RuntimeError(f"{method} failed: {resp['error']}")
        return resp["result"]

    def sign_leaf(self, leaf_hex):
        # Hardhat's eth_sign applies the EIP-191 "Ethereum Signed Message" prefix to the raw bytes.
        sig = self._request("eth_sign", [self.address, leaf_hex])
        if recover_leaf_signer(leaf_hex, sig) != self.address:
            raise RuntimeError("node signature did not recover to the attestor")
        return sig

    def sign_typed(self, typed):
        typed = json.loads(json.dumps(typed))
        sig = self._request("eth_signTypedData_v4", [self.address, json.dumps(typed)])
        if recover_typed_signer(typed, sig) != self.address:
            raise RuntimeError("node typed-data signature did not recover to the attestor")
        return sig


class EnvKeySigner:
    """Signs with a testnet key loaded from the git-ignored .env at runtime. The key is never logged."""

    kind = "env"

    def __init__(self, env_var=None):
        from dotenv import dotenv_values

        from .config import ROOT

        values = {**dotenv_values(ROOT / ".env"), **os.environ}
        names = [env_var] if env_var else ["ATTESTOR_PRIVATE_KEY", "DEPLOYER_PRIVATE_KEY"]
        key = next((values[n] for n in names if values.get(n)), None)
        if not key:
            raise RuntimeError("No attestor key: set ATTESTOR_PRIVATE_KEY in the git-ignored .env (see KEYS_NEEDED.md)")
        self._account = Account.from_key(key)
        self.address = self._account.address

    @property
    def account(self):
        return self._account

    def sign_leaf(self, leaf_hex):
        return "0x" + self._account.sign_message(encode_defunct(primitive=_bytes32(leaf_hex))).signature.hex().removeprefix("0x")

    def sign_typed(self, typed):
        return "0x" + self._account.sign_message(encode_typed_data(full_message=typed)).signature.hex().removeprefix("0x")

    def __repr__(self):
        return f"EnvKeySigner({self.address})"
