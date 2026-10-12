"""Sorted-pair keccak256 Merkle tree, compatible with OpenZeppelin MerkleProof.

Leaves are sorted before building, so the root does not depend on record order. An odd node at
the end of a level is promoted unchanged. Proofs are lists of sibling hashes, bottom-up.
"""
from eth_hash.auto import keccak


def _b(h) -> bytes:
    if isinstance(h, bytes):
        if len(h) != 32:
            raise ValueError("hash must be 32 bytes")
        return h
    h = h[2:] if h.startswith("0x") else h
    out = bytes.fromhex(h)
    if len(out) != 32:
        raise ValueError("hash must be 32 bytes")
    return out


def _hex(b: bytes) -> str:
    return "0x" + b.hex()


def hash_pair(a, b) -> bytes:
    a, b = _b(a), _b(b)
    return keccak(a + b) if a <= b else keccak(b + a)


class MerkleTree:
    def __init__(self, leaves):
        if not leaves:
            raise ValueError("a Merkle tree needs at least one leaf")
        level = sorted(_b(x) for x in leaves)
        if len(set(level)) != len(level):
            raise ValueError("duplicate leaves")
        self.levels = [level]
        while len(level) > 1:
            level = [hash_pair(level[i], level[i + 1]) if i + 1 < len(level) else level[i]
                     for i in range(0, len(level), 2)]
            self.levels.append(level)
        self._index = {leaf: i for i, leaf in enumerate(self.levels[0])}

    @property
    def root(self) -> str:
        return _hex(self.levels[-1][0])

    @property
    def leaves(self):
        return [_hex(x) for x in self.levels[0]]

    def proof(self, leaf):
        idx = self._index.get(_b(leaf))
        if idx is None:
            raise KeyError("leaf not in tree")
        out = []
        for level in self.levels[:-1]:
            sibling = idx ^ 1
            if sibling < len(level):
                out.append(_hex(level[sibling]))
            idx //= 2
        return out


def verify(proof, root, leaf) -> bool:
    node = _b(leaf)
    for sibling in proof:
        node = hash_pair(node, sibling)
    return node == _b(root)
