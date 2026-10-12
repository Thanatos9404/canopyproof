// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";
import {CarbonIntegrityOracle} from "./CarbonIntegrityOracle.sol";

/// @title GuardedCreditLedger (EXAMPLE INTEGRATION)
/// @notice A minimal credit ledger showing how a marketplace or registry could use the integrity
///         oracle: credits of a batch whose forest shows a suspected reversal cannot move, and credits
///         can only be retired while their batch is monitored, fresh and healthy.
/// @dev    Demonstration only: no real credits, no value, no transfers to external tokens.
contract GuardedCreditLedger is AccessControl {
    bytes32 public constant ISSUER_ROLE = keccak256("ISSUER_ROLE");

    CarbonIntegrityOracle public immutable oracle;

    mapping(uint256 batchId => mapping(address holder => uint256)) public balanceOf;
    mapping(uint256 batchId => uint256) public minted;
    mapping(uint256 batchId => uint256) public retired;

    event Minted(uint256 indexed batchId, address indexed to, uint256 amount);
    event Transferred(uint256 indexed batchId, address indexed from, address indexed to, uint256 amount);
    event Retired(uint256 indexed batchId, address indexed holder, uint256 amount, string beneficiary);

    error ExceedsIssued();
    error InsufficientBalance();
    /// @param check retirementCheck code: 1 unmonitored, 2 stale, 3 watch, 4 reversal suspected
    error IntegrityHold(uint8 check);

    constructor(CarbonIntegrityOracle oracle_, address admin) {
        oracle = oracle_;
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(ISSUER_ROLE, admin);
    }

    function mint(uint256 batchId, address to, uint256 amount) external onlyRole(ISSUER_ROLE) {
        uint256 issued = oracle.getCreditBatch(batchId).issuedCredits;
        if (minted[batchId] + amount > issued) revert ExceedsIssued();
        minted[batchId] += amount;
        balanceOf[batchId][to] += amount;
        emit Minted(batchId, to, amount);
    }

    /// @notice Transfers are frozen only while a reversal is suspected.
    function transfer(uint256 batchId, address to, uint256 amount) external {
        uint8 check = oracle.retirementCheck(batchId);
        if (check == 4) revert IntegrityHold(check);
        _move(batchId, msg.sender, to, amount);
    }

    /// @notice Retirement needs a fresh, healthy integrity status.
    function retire(uint256 batchId, uint256 amount, string calldata beneficiary) external {
        uint8 check = oracle.retirementCheck(batchId);
        if (check != 0) revert IntegrityHold(check);
        if (balanceOf[batchId][msg.sender] < amount) revert InsufficientBalance();
        balanceOf[batchId][msg.sender] -= amount;
        retired[batchId] += amount;
        emit Retired(batchId, msg.sender, amount, beneficiary);
    }

    function _move(uint256 batchId, address from, address to, uint256 amount) internal {
        if (balanceOf[batchId][from] < amount) revert InsufficientBalance();
        balanceOf[batchId][from] -= amount;
        balanceOf[batchId][to] += amount;
        emit Transferred(batchId, from, to, amount);
    }
}
