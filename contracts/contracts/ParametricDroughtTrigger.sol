// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";
import {FieldProofRegistry} from "./FieldProofRegistry.sol";

/// @title ParametricDroughtTrigger (PROTOTYPE)
/// @notice Evaluates a transparent, pre-agreed drought rule against observations that were
///         anchored in FieldProofRegistry. An acquisition qualifies when the plot is cloud-free,
///         rainfall over the previous 60 days is deficient AND vegetation is anomalously low by
///         either measure: VCI below a threshold, or NDVI far below the plot's seasonal model while a
///         crop is normally present. When enough qualifying observations for one plot fall
///         inside the program window, the program flips to "triggered" and emits an event that an
///         insurer, cooperative or climate-finance program could act on.
/// @dev    PROTOTYPE ONLY. This contract holds no funds and pays nothing. The thresholds are
///         illustrative (VCI below 35 follows Kogan 1995; a seasonal z-score of -2 is a two-sigma drop;
///         a rainfall departure of -20% or worse is the India Meteorological Department "deficient"
///         category). They are not an actuarially
///         calibrated index and satellite vegetation indices cannot establish crop loss by themselves.
///         Evidence submission is permissionless: trust comes from the anchored Merkle root,
///         not from the submitter.
contract ParametricDroughtTrigger is AccessControl {
    bytes32 public constant PROGRAM_ADMIN_ROLE = keccak256("PROGRAM_ADMIN_ROLE");

    FieldProofRegistry public immutable registry;

    struct ProgramInput {
        bytes32 plotKey;
        uint64 windowStart; // unix seconds (inclusive)
        uint64 windowEnd; // unix seconds (inclusive)
        int32 maxVciBps; // vegetation low when vciBps < maxVciBps ...
        int32 maxNdviZE2; // ... or when ndviZE2 <= maxNdviZE2 (with FLAG_CROP_EXPECTED)
        int32 maxRainDepartureBps; // qualifies when rainDeparture60dBps <= maxRainDepartureBps
        uint32 minValidFractionBps; // minimum cloud-free share of the plot
        uint8 requiredObservations; // distinct qualifying acquisitions needed
        string terms; // human-readable terms or a URI to them
    }

    struct Program {
        bytes32 plotKey;
        uint64 windowStart;
        uint64 windowEnd;
        int32 maxVciBps;
        int32 maxNdviZE2;
        int32 maxRainDepartureBps;
        uint32 minValidFractionBps;
        uint8 requiredObservations;
        uint8 qualifyingCount;
        bool triggered;
        uint64 triggeredAt;
        address creator;
        string terms;
    }

    /// Reasons an observation does not qualify (0 = qualifies).
    uint8 public constant OK = 0;
    uint8 public constant WRONG_PLOT = 1;
    uint8 public constant OUTSIDE_WINDOW = 2;
    uint8 public constant NOT_CLEAR = 3;
    uint8 public constant LOW_VALID_FRACTION = 4;
    uint8 public constant VEGETATION_UNAVAILABLE = 5;
    uint8 public constant VEGETATION_NOT_LOW = 6;
    uint8 public constant RAIN_UNAVAILABLE = 7;
    uint8 public constant RAIN_NOT_DEFICIENT = 8;
    uint8 public constant ALREADY_COUNTED = 9;
    uint8 public constant ALREADY_TRIGGERED = 10;

    // Mirrors FieldProofRegistry flag bits (kept as constants to avoid external calls).
    uint8 private constant FLAG_QUALITY_CLEAR = 1;
    uint8 private constant FLAG_VCI_AVAILABLE = 2;
    uint8 private constant FLAG_RAIN_AVAILABLE = 4;
    uint8 private constant FLAG_ZSCORE_AVAILABLE = 8;
    uint8 private constant FLAG_CROP_EXPECTED = 16;

    Program[] private _programs;

    /// programId => keccak256(plotKey, observedAt) => counted. One acquisition counts once,
    /// even if the same observation is anchored again in a later batch.
    mapping(uint256 programId => mapping(bytes32 evidenceKey => bool)) public evidenceCounted;

    event ProgramCreated(
        uint256 indexed programId,
        bytes32 indexed plotKey,
        uint64 windowStart,
        uint64 windowEnd,
        int32 maxVciBps,
        int32 maxNdviZE2,
        int32 maxRainDepartureBps,
        uint8 requiredObservations,
        string terms
    );
    event EvidenceAccepted(
        uint256 indexed programId,
        uint256 indexed batchId,
        bytes32 indexed leaf,
        uint64 observedAt,
        int32 vciBps,
        int32 ndviZE2,
        int32 rainDeparture60dBps,
        uint8 qualifyingCount,
        address submitter
    );
    event DroughtTriggered(uint256 indexed programId, bytes32 indexed plotKey, uint64 triggeredAt, uint8 qualifyingCount);

    error UnknownProgram(uint256 programId);
    error InvalidProgram();
    error EvidenceRejected(uint8 reason);
    error InvalidProof();

    constructor(FieldProofRegistry registry_, address admin) {
        registry = registry_;
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(PROGRAM_ADMIN_ROLE, admin);
    }

    function createProgram(ProgramInput calldata p) external onlyRole(PROGRAM_ADMIN_ROLE) returns (uint256 programId) {
        if (
            p.plotKey == bytes32(0) || p.windowEnd < p.windowStart || p.requiredObservations == 0
                || p.minValidFractionBps > 10_000
        ) revert InvalidProgram();
        _programs.push(
            Program({
                plotKey: p.plotKey,
                windowStart: p.windowStart,
                windowEnd: p.windowEnd,
                maxVciBps: p.maxVciBps,
                maxNdviZE2: p.maxNdviZE2,
                maxRainDepartureBps: p.maxRainDepartureBps,
                minValidFractionBps: p.minValidFractionBps,
                requiredObservations: p.requiredObservations,
                qualifyingCount: 0,
                triggered: false,
                triggeredAt: 0,
                creator: msg.sender,
                terms: p.terms
            })
        );
        programId = _programs.length;
        emit ProgramCreated(
            programId,
            p.plotKey,
            p.windowStart,
            p.windowEnd,
            p.maxVciBps,
            p.maxNdviZE2,
            p.maxRainDepartureBps,
            p.requiredObservations,
            p.terms
        );
    }

    /// @notice Pure rule check, without Merkle verification. Useful for dashboards.
    function evaluate(uint256 programId, FieldProofRegistry.Observation calldata o) public view returns (uint8) {
        Program storage p = _program(programId);
        if (p.triggered) return ALREADY_TRIGGERED;
        if (o.plotKey != p.plotKey) return WRONG_PLOT;
        if (o.observedAt < p.windowStart || o.observedAt > p.windowEnd) return OUTSIDE_WINDOW;
        if ((o.flags & FLAG_QUALITY_CLEAR) == 0) return NOT_CLEAR;
        if (o.validFractionBps < p.minValidFractionBps) return LOW_VALID_FRACTION;
        bool hasVci = (o.flags & FLAG_VCI_AVAILABLE) != 0;
        bool hasZ = (o.flags & FLAG_ZSCORE_AVAILABLE) != 0 && (o.flags & FLAG_CROP_EXPECTED) != 0;
        if (!hasVci && !hasZ) return VEGETATION_UNAVAILABLE;
        bool vciLow = hasVci && o.vciBps < p.maxVciBps;
        bool zLow = hasZ && o.ndviZE2 <= p.maxNdviZE2;
        if (!vciLow && !zLow) return VEGETATION_NOT_LOW;
        if ((o.flags & FLAG_RAIN_AVAILABLE) == 0) return RAIN_UNAVAILABLE;
        if (o.rainDeparture60dBps > p.maxRainDepartureBps) return RAIN_NOT_DEFICIENT;
        if (evidenceCounted[programId][evidenceKey(o.plotKey, o.observedAt)]) return ALREADY_COUNTED;
        return OK;
    }

    /// @notice Submit one anchored observation as evidence. Anyone may call this.
    function submitEvidence(
        uint256 programId,
        uint256 batchId,
        FieldProofRegistry.Observation calldata o,
        bytes32[] calldata proof
    ) external returns (bool triggered) {
        uint8 reason = evaluate(programId, o);
        if (reason != OK) revert EvidenceRejected(reason);
        if (!registry.verifyObservation(batchId, o, proof)) revert InvalidProof();

        Program storage p = _programs[programId - 1];
        evidenceCounted[programId][evidenceKey(o.plotKey, o.observedAt)] = true;
        p.qualifyingCount += 1;
        emit EvidenceAccepted(
            programId,
            batchId,
            registry.observationLeaf(o),
            o.observedAt,
            o.vciBps,
            o.ndviZE2,
            o.rainDeparture60dBps,
            p.qualifyingCount,
            msg.sender
        );
        if (p.qualifyingCount >= p.requiredObservations) {
            p.triggered = true;
            p.triggeredAt = uint64(block.timestamp);
            emit DroughtTriggered(programId, p.plotKey, p.triggeredAt, p.qualifyingCount);
        }
        return p.triggered;
    }

    function evidenceKey(bytes32 plotKey, uint64 observedAt) public pure returns (bytes32) {
        return keccak256(abi.encode(plotKey, observedAt));
    }

    function programCount() external view returns (uint256) {
        return _programs.length;
    }

    function getProgram(uint256 programId) external view returns (Program memory) {
        return _program(programId);
    }

    function _program(uint256 programId) internal view returns (Program storage) {
        if (programId == 0 || programId > _programs.length) revert UnknownProgram(programId);
        return _programs[programId - 1];
    }
}
