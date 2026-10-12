// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";
import {EIP712} from "@openzeppelin/contracts/utils/cryptography/EIP712.sol";
import {ECDSA} from "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import {MerkleProof} from "@openzeppelin/contracts/utils/cryptography/MerkleProof.sol";

/// @title FieldProofRegistry
/// @notice Anchors the Merkle roots of batches of content-hashed, signed field observation
///         records (Sentinel-2 vegetation indices + ERA5 rainfall context for smallholder plots).
///         Anyone can later prove that a single record belongs to an anchored batch.
/// @dev    Records stay off-chain. Each leaf commits to a small set of fixed-point numbers that a
///         contract can reason about, plus `documentHash`, the keccak256 of the full canonical
///         JSON record. Leaves use the OpenZeppelin "double hash" convention and sorted-pair
///         Merkle trees, so proofs verify with `MerkleProof`.
///         Plot coordinates never go on-chain: plots are identified by a salted commitment.
contract FieldProofRegistry is AccessControl, EIP712 {
    /// @notice Role allowed to anchor batches (the data pipeline operator / oracle key).
    bytes32 public constant ATTESTOR_ROLE = keccak256("ATTESTOR_ROLE");

    /// @notice EIP-712 type used when an attestor signs a batch off-chain and a relayer submits it.
    bytes32 public constant BATCH_TYPEHASH = keccak256(
        "Batch(bytes32 merkleRoot,bytes32 manifestHash,uint32 recordCount,uint64 periodStart,uint64 periodEnd,string uri,uint256 nonce)"
    );

    /// @notice Bit flags carried in every observation leaf.
    uint8 public constant FLAG_QUALITY_CLEAR = 1; // enough cloud-free pixels inside the plot
    uint8 public constant FLAG_VCI_AVAILABLE = 2; // multi-year Vegetation Condition Index computed
    uint8 public constant FLAG_RAIN_AVAILABLE = 4; // 60-day rainfall departure computed
    uint8 public constant FLAG_ZSCORE_AVAILABLE = 8; // NDVI seasonal-model z-score computed
    uint8 public constant FLAG_CROP_EXPECTED = 16; // a crop is normally present on this date

    struct BatchInput {
        bytes32 merkleRoot;
        bytes32 manifestHash; // keccak256 of the canonical batch manifest JSON
        uint32 recordCount;
        uint64 periodStart; // unix seconds, first acquisition covered
        uint64 periodEnd; // unix seconds, last acquisition covered
        string uri; // where the manifest + records can be fetched (repo path, IPFS CID, ...)
    }

    struct Batch {
        bytes32 merkleRoot;
        bytes32 manifestHash;
        address attestor;
        uint64 periodStart;
        uint64 periodEnd;
        uint64 anchoredAt;
        uint32 recordCount;
        bool revoked;
        string uri;
    }

    /// @notice Values committed in every leaf. Decimals are fixed-point integers:
    ///         E4 = value x 10^4, Bps = value x 10^4 of a fraction (percent x 100).
    struct Observation {
        bytes32 plotKey; // salted commitment to the plot geometry
        uint64 observedAt; // satellite acquisition time, unix seconds
        int32 ndviE4; // mean NDVI over valid pixels x 10^4
        int32 ndmiE4; // mean NDMI over valid pixels x 10^4
        uint32 validFractionBps; // share of plot pixels that were cloud-free vegetation/soil
        int32 vciBps; // Vegetation Condition Index (0..10000) when FLAG_VCI_AVAILABLE
        int32 ndviZE2; // NDVI departure from the plot's seasonal model, robust z x 100
        int32 rainDeparture60dBps; // 60-day rainfall departure from 1991-2020 normal (-10000 = no rain)
        uint8 stressLevel; // 0 insufficient data, 1 normal, 2 watch, 3 alert
        uint8 flags;
        bytes32 documentHash; // keccak256(canonical JSON record)
    }

    Batch[] private _batches;

    /// @notice batchId (1-based) for an anchored Merkle root, 0 if unknown. Prevents double anchoring.
    mapping(bytes32 merkleRoot => uint256 batchId) public batchIdByRoot;

    /// @notice Replay protection for relayed (signature-based) anchoring.
    mapping(address attestor => uint256 nonce) public nonces;

    event BatchAnchored(
        uint256 indexed batchId,
        bytes32 indexed merkleRoot,
        address indexed attestor,
        bytes32 manifestHash,
        uint32 recordCount,
        uint64 periodStart,
        uint64 periodEnd,
        string uri,
        address submitter
    );
    event BatchRevoked(uint256 indexed batchId, address indexed by, string reason);

    error ZeroAddress();
    error EmptyRoot();
    error EmptyBatch();
    error InvalidPeriod();
    error RootAlreadyAnchored(uint256 batchId);
    error UnknownBatch(uint256 batchId);
    error AlreadyRevoked(uint256 batchId);
    error NotAuthorized();
    error InvalidSignature(address recovered);

    constructor(address admin) EIP712("FieldProofRegistry", "1") {
        if (admin == address(0)) revert ZeroAddress();
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
    }

    // ------------------------------------------------------------------ anchoring

    /// @notice Anchor a batch directly from an attestor account.
    function anchorBatch(BatchInput calldata input) external onlyRole(ATTESTOR_ROLE) returns (uint256) {
        return _anchor(input, msg.sender);
    }

    /// @notice Anchor a batch signed off-chain (EIP-712) by an attestor. Any account may relay it,
    ///         so a field app can pay gas on behalf of the data operator or vice versa.
    function anchorBatchWithSignature(BatchInput calldata input, address attestor, bytes calldata signature)
        external
        returns (uint256)
    {
        bytes32 digest = batchDigest(input, nonces[attestor]);
        address recovered = ECDSA.recover(digest, signature);
        if (recovered != attestor || !hasRole(ATTESTOR_ROLE, attestor)) revert InvalidSignature(recovered);
        unchecked {
            nonces[attestor] += 1;
        }
        return _anchor(input, attestor);
    }

    /// @notice Mark a batch as superseded (for example after a processing bug). History is kept;
    ///         verification against a revoked batch returns false.
    function revokeBatch(uint256 batchId, string calldata reason) external {
        Batch storage b = _batch(batchId);
        if (msg.sender != b.attestor && !hasRole(DEFAULT_ADMIN_ROLE, msg.sender)) revert NotAuthorized();
        if (b.revoked) revert AlreadyRevoked(batchId);
        b.revoked = true;
        emit BatchRevoked(batchId, msg.sender, reason);
    }

    function _anchor(BatchInput calldata input, address attestor) internal returns (uint256 batchId) {
        if (input.merkleRoot == bytes32(0)) revert EmptyRoot();
        if (input.recordCount == 0) revert EmptyBatch();
        if (input.periodEnd < input.periodStart) revert InvalidPeriod();
        uint256 existing = batchIdByRoot[input.merkleRoot];
        if (existing != 0) revert RootAlreadyAnchored(existing);

        _batches.push(
            Batch({
                merkleRoot: input.merkleRoot,
                manifestHash: input.manifestHash,
                attestor: attestor,
                periodStart: input.periodStart,
                periodEnd: input.periodEnd,
                anchoredAt: uint64(block.timestamp),
                recordCount: input.recordCount,
                revoked: false,
                uri: input.uri
            })
        );
        batchId = _batches.length;
        batchIdByRoot[input.merkleRoot] = batchId;
        emit BatchAnchored(
            batchId,
            input.merkleRoot,
            attestor,
            input.manifestHash,
            input.recordCount,
            input.periodStart,
            input.periodEnd,
            input.uri,
            msg.sender
        );
    }

    // ------------------------------------------------------------------ verification

    /// @notice Leaf for an observation: keccak256(bytes.concat(keccak256(abi.encode(o)))).
    function observationLeaf(Observation calldata o) public pure returns (bytes32) {
        return keccak256(bytes.concat(keccak256(abi.encode(o))));
    }

    /// @notice True when `o` is included in batch `batchId` and the batch is not revoked.
    function verifyObservation(uint256 batchId, Observation calldata o, bytes32[] calldata proof)
        external
        view
        returns (bool)
    {
        return verifyLeaf(batchId, observationLeaf(o), proof);
    }

    function verifyLeaf(uint256 batchId, bytes32 leaf, bytes32[] calldata proof) public view returns (bool) {
        if (batchId == 0 || batchId > _batches.length) return false;
        Batch storage b = _batches[batchId - 1];
        if (b.revoked) return false;
        return MerkleProof.verifyCalldata(proof, b.merkleRoot, leaf);
    }

    // ------------------------------------------------------------------ views

    function batchCount() external view returns (uint256) {
        return _batches.length;
    }

    function getBatch(uint256 batchId) external view returns (Batch memory) {
        return _batch(batchId);
    }

    /// @notice EIP-712 digest an attestor signs for `anchorBatchWithSignature`.
    function batchDigest(BatchInput calldata input, uint256 nonce) public view returns (bytes32) {
        return _hashTypedDataV4(
            keccak256(
                abi.encode(
                    BATCH_TYPEHASH,
                    input.merkleRoot,
                    input.manifestHash,
                    input.recordCount,
                    input.periodStart,
                    input.periodEnd,
                    keccak256(bytes(input.uri)),
                    nonce
                )
            )
        );
    }

    function domainSeparator() external view returns (bytes32) {
        return _domainSeparatorV4();
    }

    function _batch(uint256 batchId) internal view returns (Batch storage) {
        if (batchId == 0 || batchId > _batches.length) revert UnknownBatch(batchId);
        return _batches[batchId - 1];
    }
}
