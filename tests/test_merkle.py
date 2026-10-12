import random

import pytest
from eth_hash.auto import keccak

from fieldproof.merkle import MerkleTree, hash_pair, verify


def leaves(n, seed=1):
    rnd = random.Random(seed)
    return ["0x" + keccak(rnd.randbytes(16)).hex() for _ in range(n)]


@pytest.mark.parametrize("n", list(range(1, 18)) + [64, 101])
def test_every_proof_verifies(n):
    ls = leaves(n)
    tree = MerkleTree(ls)
    for leaf in ls:
        assert verify(tree.proof(leaf), tree.root, leaf)


def test_root_is_order_independent_and_single_leaf_is_root():
    ls = leaves(9)
    assert MerkleTree(ls).root == MerkleTree(list(reversed(ls))).root
    assert MerkleTree(ls[:1]).root == ls[0]
    assert MerkleTree(ls[:1]).proof(ls[0]) == []


def test_tampering_fails():
    ls = leaves(10)
    tree = MerkleTree(ls)
    proof = tree.proof(ls[3])
    assert not verify(proof, tree.root, ls[4])
    forged = "0x" + keccak(b"forged").hex()
    assert not verify(proof, tree.root, forged)
    assert not verify(proof[:-1], tree.root, ls[3])


def test_sorted_pair_hash_is_commutative():
    a, b = leaves(2)
    assert hash_pair(a, b) == hash_pair(b, a)


def test_rejects_empty_and_duplicates():
    with pytest.raises(ValueError):
        MerkleTree([])
    with pytest.raises(ValueError):
        MerkleTree([leaves(1)[0]] * 2)
    with pytest.raises(KeyError):
        MerkleTree(leaves(3)).proof(leaves(1, seed=99)[0])
