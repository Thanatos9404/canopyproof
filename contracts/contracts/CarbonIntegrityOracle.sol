// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";
import {CanopyProofRegistry} from "./CanopyProofRegistry.sol";

/// @title CarbonIntegrityOracle (PROTOTYPE)
/// @notice Public, continuously updated integrity status for forest carbon credit batches.
///
///         1. A registrar registers a credit batch: project, vintage years, issued credits, the
///            monitoring cells that sample it, and the ~1 km land-grid cells it claims. The same land
///            cannot be claimed twice for the same vintage year, by any batch.
///         2. Anyone may submit a Sentinel-2 observation that was anchored in CanopyProofRegistry.
///            The contract checks the Merkle proof, refuses stale or replayed evidence, and derives the
///            cell's status from the measured fractions using the batch's own thresholds.
///         3. A first "reversal suspected" emits ReversalSuspected and BufferPoolReviewFlagged events
///            that a registry, buffer-pool manager or marketplace can act on. Reversals are sticky
///            until an auditor resolves them with a written reason.
///         4. Marketplaces read integrityStatus() / retirementCheck() before trading or retiring.
///
/// @dev    PROTOTYPE. Holds no funds. Satellite change is evidence for review, not proof of carbon
///         loss: permitted harvest, storms and phenology can look like change, and nothing here
///         judges additionality or baselines. Thresholds are per batch and illustrative.
contract CarbonIntegrityOracle is AccessControl {
    bytes32 public constant REGISTRAR_ROLE = keccak256("REGISTRAR_ROLE");
    bytes32 public constant AUDITOR_ROLE = keccak256("AUDITOR_ROLE");

    enum Status {
        Unmonitored,
        Healthy,
        Watch,
        ReversalSuspected
    }

    uint8 private constant FLAG_CLEAR = 1;
    uint8 private constant FLAG_BASELINE_READY = 2;
    uint8 public constant MAX_SITES = 32;
    uint8 public constant MAX_LAND_CELLS = 64;
    uint16 public constant MAX_VINTAGE_SPAN = 20;

    CanopyProofRegistry public immutable registry;

    struct CreditBatchInput {
        bytes32 projectKey;
        string registryRef; // e.g. "ACR273"
        uint16 vintageStart;
        uint16 vintageEnd;
        uint64 issuedCredits;
        bytes32[] siteKeys; // monitoring cells sampling the project
        bytes32[] landCells; // keccak256 ids of the ~1 km land-grid cells claimed
        uint32 watchBps; // e.g. 200 = 2% of forest pixels
        uint32 reversalBps; // e.g. 500 = 5% of baseline forest with confirmed loss
        uint64 maxEvidenceAge; // seconds before the status counts as stale
        string uri; // project documents
    }

    struct CreditBatch {
        bytes32 projectKey;
        uint16 vintageStart;
        uint16 vintageEnd;
        uint64 issuedCredits;
        uint32 watchBps;
        uint32 reversalBps;
        uint64 maxEvidenceAge;
        uint64 registeredAt;
        address registrar;
        bool bufferReviewFlagged;
        string registryRef;
        string uri;
    }

    struct SiteState {
        Status status;
        uint64 lastObservedAt;
        uint32 cumulativeLossBps;
        uint32 disturbedBps;
        uint32 burnBps;
        uint256 lastRegistryBatch;
        bytes32 lastLeaf;
    }

    CreditBatch[] private _batches;
    mapping(uint256 batchId => bytes32[]) private _sites;
    mapping(uint256 batchId => bytes32[]) private _landCells;
    mapping(uint256 batchId => mapping(bytes32 siteKey => bool)) public isSiteOf;
    mapping(uint256 batchId => mapping(bytes32 siteKey => SiteState)) private _siteState;
    /// @notice land cell => vintage year => credit batch that claimed it (0 = unclaimed)
    mapping(bytes32 landCell => mapping(uint16 year => uint256 batchId)) public landClaim;

    event CreditBatchRegistered(
        uint256 indexed batchId,
        bytes32 indexed projectKey,
        string registryRef,
        uint16 vintageStart,
        uint16 vintageEnd,
        uint64 issuedCredits,
        uint256 siteCount,
        uint256 landCellCount
    );
    event EvidenceAccepted(
        uint256 indexed batchId,
        bytes32 indexed siteKey,
        bytes32 indexed leaf,
        uint256 registryBatchId,
        uint64 observedAt,
        Status status,
        uint32 cumulativeLossBps,
        address submitter
    );
    event StatusChanged(uint256 indexed batchId, bytes32 indexed siteKey, Status from, Status to, uint64 observedAt);
    event ReversalSuspected(
        uint256 indexed batchId, bytes32 indexed siteKey, uint64 observedAt, uint32 cumulativeLossBps, uint64 indicativeAffectedCredits
    );
    event BufferPoolReviewFlagged(uint256 indexed batchId, string registryRef, uint64 indicativeAffectedCredits, uint64 observedAt);
    event SiteResolved(uint256 indexed batchId, bytes32 indexed siteKey, Status from, Status to, address auditor, string reason);

    error UnknownBatch(uint256 batchId);
    error InvalidBatch();
    error DuplicateSite(bytes32 siteKey);
    error LandAlreadyClaimed(bytes32 landCell, uint16 year, uint256 existingBatchId);
    error WrongProject();
    error UnknownSite(bytes32 siteKey);
    error NotMeasurable();
    error StaleEvidence(uint64 lastObservedAt);
    error InvalidProof();

    constructor(CanopyProofRegistry registry_, address admin) {
        registry = registry_;
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(REGISTRAR_ROLE, admin);
        _grantRole(AUDITOR_ROLE, admin);
    }

    // ------------------------------------------------------------------ registration

    /// @notice Register a credit batch. Reverts with LandAlreadyClaimed if any land cell is already
    ///         claimed for any of the vintage years: the same forest cannot back two credits for one year.
    function registerCreditBatch(CreditBatchInput calldata b) external onlyRole(REGISTRAR_ROLE) returns (uint256 batchId) {
        if (
            b.projectKey == bytes32(0) || b.vintageEnd < b.vintageStart || b.vintageEnd - b.vintageStart > MAX_VINTAGE_SPAN
                || b.siteKeys.length == 0 || b.siteKeys.length > MAX_SITES || b.landCells.length > MAX_LAND_CELLS
                || b.watchBps == 0 || b.watchBps > b.reversalBps || b.reversalBps > 10_000 || b.maxEvidenceAge == 0
        ) revert InvalidBatch();

        _batches.push(
            CreditBatch({
                projectKey: b.projectKey,
                vintageStart: b.vintageStart,
                vintageEnd: b.vintageEnd,
                issuedCredits: b.issuedCredits,
                watchBps: b.watchBps,
                reversalBps: b.reversalBps,
                maxEvidenceAge: b.maxEvidenceAge,
                registeredAt: uint64(block.timestamp),
                registrar: msg.sender,
                bufferReviewFlagged: false,
                registryRef: b.registryRef,
                uri: b.uri
            })
        );
        batchId = _batches.length;

        for (uint256 i = 0; i < b.landCells.length; i++) {
            for (uint16 y = b.vintageStart; y <= b.vintageEnd; y++) {
                uint256 existing = landClaim[b.landCells[i]][y];
                if (existing != 0) revert LandAlreadyClaimed(b.landCells[i], y, existing);
                landClaim[b.landCells[i]][y] = batchId;
            }
            _landCells[batchId].push(b.landCells[i]);
        }
        for (uint256 i = 0; i < b.siteKeys.length; i++) {
            if (isSiteOf[batchId][b.siteKeys[i]]) revert DuplicateSite(b.siteKeys[i]);
            isSiteOf[batchId][b.siteKeys[i]] = true;
            _sites[batchId].push(b.siteKeys[i]);
        }
        emit CreditBatchRegistered(
            batchId, b.projectKey, b.registryRef, b.vintageStart, b.vintageEnd, b.issuedCredits, b.siteKeys.length, b.landCells.length
        );
    }

    // ------------------------------------------------------------------ evidence

    /// @notice Status the batch's thresholds assign to one observation (no state change).
    function previewStatus(uint256 batchId, CanopyProofRegistry.Observation calldata o) public view returns (Status) {
        CreditBatch storage b = _batch(batchId);
        if ((o.flags & FLAG_CLEAR) == 0 || (o.flags & FLAG_BASELINE_READY) == 0) return Status.Unmonitored;
        if (o.cumulativeLossBps >= b.reversalBps) return Status.ReversalSuspected;
        if (o.disturbedBps >= b.watchBps || o.burnBps >= b.watchBps || o.cumulativeLossBps >= b.watchBps) {
            return Status.Watch;
        }
        return Status.Healthy;
    }

    /// @notice Submit one anchored observation for a credit batch. Anyone may call this: trust comes
    ///         from the anchored Merkle root, not from the submitter. Evidence must be newer than the
    ///         cell's last accepted evidence, so old "healthy" records cannot hide a later reversal.
    function submitEvidence(
        uint256 batchId,
        uint256 registryBatchId,
        CanopyProofRegistry.Observation calldata o,
        bytes32[] calldata proof
    ) external returns (Status next) {
        CreditBatch storage b = _batch(batchId);
        if (o.projectKey != b.projectKey) revert WrongProject();
        if (!isSiteOf[batchId][o.siteKey]) revert UnknownSite(o.siteKey);
        if ((o.flags & FLAG_CLEAR) == 0 || (o.flags & FLAG_BASELINE_READY) == 0) revert NotMeasurable();
        SiteState storage s = _siteState[batchId][o.siteKey];
        if (o.observedAt <= s.lastObservedAt) revert StaleEvidence(s.lastObservedAt);
        if (!registry.verifyObservation(registryBatchId, o, proof)) revert InvalidProof();

        Status prev = s.status;
        Status computed = previewStatus(batchId, o);
        next = prev == Status.ReversalSuspected ? Status.ReversalSuspected : computed;
        s.status = next;
        s.lastObservedAt = o.observedAt;
        s.cumulativeLossBps = o.cumulativeLossBps;
        s.disturbedBps = o.disturbedBps;
        s.burnBps = o.burnBps;
        s.lastRegistryBatch = registryBatchId;
        bytes32 leaf = registry.observationLeaf(o);
        s.lastLeaf = leaf;

        emit EvidenceAccepted(batchId, o.siteKey, leaf, registryBatchId, o.observedAt, next, o.cumulativeLossBps, msg.sender);
        if (next != prev) emit StatusChanged(batchId, o.siteKey, prev, next, o.observedAt);
        if (next == Status.ReversalSuspected && prev != Status.ReversalSuspected) {
            uint64 affected = indicativeAffectedCredits(batchId);
            emit ReversalSuspected(batchId, o.siteKey, o.observedAt, o.cumulativeLossBps, affected);
            if (!b.bufferReviewFlagged) {
                b.bufferReviewFlagged = true;
                emit BufferPoolReviewFlagged(batchId, b.registryRef, affected, o.observedAt);
            }
        }
    }

    /// @notice Auditor override after a field check, e.g. harvest within the project's rules
    ///         (back to Healthy) or a confirmed loss. Always leaves a public reason.
    function resolveSite(uint256 batchId, bytes32 siteKey, Status to, string calldata reason) external onlyRole(AUDITOR_ROLE) {
        _batch(batchId);
        if (!isSiteOf[batchId][siteKey]) revert UnknownSite(siteKey);
        SiteState storage s = _siteState[batchId][siteKey];
        Status from = s.status;
        s.status = to;
        emit SiteResolved(batchId, siteKey, from, to, msg.sender, reason);
        if (from != to) emit StatusChanged(batchId, siteKey, from, to, s.lastObservedAt);
    }

    // ------------------------------------------------------------------ views for marketplaces

    /// @notice Worst status across the batch's monitoring cells, the oldest "last seen" time, whether
    ///         any cell is stale or unmonitored, and the mean confirmed-loss share across cells.
    function integrityStatus(uint256 batchId)
        public
        view
        returns (Status status, uint64 oldestObservation, bool stale, uint32 indicativeAffectedBps, uint256 sitesMonitored)
    {
        CreditBatch storage b = _batch(batchId);
        bytes32[] storage sites = _sites[batchId];
        uint256 lossSum;
        oldestObservation = type(uint64).max;
        for (uint256 i = 0; i < sites.length; i++) {
            SiteState storage s = _siteState[batchId][sites[i]];
            if (s.lastObservedAt == 0) {
                stale = true;
                oldestObservation = 0;
                continue;
            }
            sitesMonitored++;
            if (uint8(s.status) > uint8(status)) status = s.status;
            if (s.lastObservedAt < oldestObservation) oldestObservation = s.lastObservedAt;
            if (block.timestamp > s.lastObservedAt + b.maxEvidenceAge) stale = true;
            lossSum += s.cumulativeLossBps;
        }
        if (sitesMonitored == 0) oldestObservation = 0;
        indicativeAffectedBps = uint32(lossSum / sites.length);
    }

    /// @notice Issued credits x mean confirmed-loss share across the sampled cells. Indicative only:
    ///         a sample of 1 km cells, not a carbon stock estimate.
    function indicativeAffectedCredits(uint256 batchId) public view returns (uint64) {
        (,,, uint32 bps,) = integrityStatus(batchId);
        return uint64((uint256(_batches[batchId - 1].issuedCredits) * bps) / 10_000);
    }

    /// @notice 0 = ok to retire, 1 = unmonitored, 2 = stale evidence, 3 = watch, 4 = reversal suspected.
    function retirementCheck(uint256 batchId) external view returns (uint8) {
        (Status status,, bool stale,, uint256 monitored) = integrityStatus(batchId);
        if (status == Status.ReversalSuspected) return 4;
        if (monitored == 0) return 1;
        if (stale) return 2;
        if (status == Status.Watch) return 3;
        return 0;
    }

    function siteState(uint256 batchId, bytes32 siteKey) external view returns (SiteState memory) {
        return _siteState[batchId][siteKey];
    }

    function sitesOf(uint256 batchId) external view returns (bytes32[] memory) {
        return _sites[batchId];
    }

    function landCellsOf(uint256 batchId) external view returns (bytes32[] memory) {
        return _landCells[batchId];
    }

    function creditBatchCount() external view returns (uint256) {
        return _batches.length;
    }

    function getCreditBatch(uint256 batchId) external view returns (CreditBatch memory) {
        return _batch(batchId);
    }

    function _batch(uint256 batchId) internal view returns (CreditBatch storage) {
        if (batchId == 0 || batchId > _batches.length) revert UnknownBatch(batchId);
        return _batches[batchId - 1];
    }
}
