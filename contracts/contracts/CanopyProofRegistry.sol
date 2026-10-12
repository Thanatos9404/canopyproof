// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";
import {EIP712} from "@openzeppelin/contracts/utils/cryptography/EIP712.sol";
import {ECDSA} from "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import {MerkleProof} from "@openzeppelin/contracts/utils/cryptography/MerkleProof.sol";

/// @title CanopyProofRegistry
/// @notice Anchors the Merkle roots of batches of content-hashed, signed forest observation records
///         (Sentinel-2 change detection over carbon-project monitoring cells). Anyone can later prove
///         that a single record belongs to an anchored batch.
/// @dev    Records stay off-chain. Each leaf commits to the fixed-point change metrics a contract can
///         reason about, plus `documentHash`, the keccak256 of the full canonical JSON record. Leaves use
///         the OpenZeppelin "double hash" convention and sorted-pair Merkle trees (MerkleProof).
///         Monitoring cells are identified by salted commitments, not coordinates.
contract CanopyProofRegistry is AccessControl, EIP712 {
    /// @notice Role allowed to anchor batches (the data pipeline operator / oracle key).
    bytes32 public constant ATTESTOR_ROLE = keccak256("ATTESTOR_ROLE");

    /// @notice EIP-712 type used when an attestor signs a batch off-chain and a relayer submits it.
    bytes32 public constant BATCH_TYPEHASH = keccak256(
        "Batch(bytes32 merkleRoot,bytes32 manifestHash,uint32 recordCount,uint64 periodStart,uint64 periodEnd,string uri,uint256 nonce)"
    );

    /// @notice Bit flags carried in every observation leaf.
    uint8 public constant FLAG_CLEAR = 1; // enough cloud-free pixels in the cell
    uint8 public constant FLAG_BASELINE_READY = 2; // a pre-issuance forest baseline exists and was compared
    uint8 public constant FLAG_ML_UNUSUAL = 4; // advisory Isolation Forest flag (not used by contracts)
    uint8 public constant FLAG_BASELINE_PERIOD = 8; // acquisition falls inside the baseline period

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

    /// @notice Values committed in every leaf. Fractions are basis points (1/10000) of forest pixels.
    struct Observation {
        bytes32 siteKey; // salted commitment to the monitoring cell
        bytes32 projectKey; // keccak256 of the public registry project id
        uint64 observedAt; // Sentinel-2 acquisition time, unix seconds
        int32 ndviE4; // mean NDVI x 10^4
        int32 nbrE4; // mean Normalized Burn Ratio x 10^4
        uint32 validFractionBps; // share of the cell that was cloud-free land
        uint32 disturbedBps; // visible forest pixels changed sharply vs the baseline
        uint32 persistentBps; // ... and also in the previous clear look
        uint32 burnBps; // visible forest pixels with a burn-like signal
        uint32 cumulativeLossBps; // baseline forest with confirmed (2-look) loss so far
        uint8 status; // off-chain label: 0 insufficient, 1 healthy, 2 watch, 3 reversal suspected
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

    constructor(address admin) EIP712("CanopyProofRegistry", "1") {
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
