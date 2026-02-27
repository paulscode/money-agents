"""
Bitcoin Budget Schemas — request/response models for budget operations.

Used by:
- Bitcoin spend approval endpoints
- Budget dashboard endpoints
- Spend Advisor agent
"""
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Bitcoin Transaction schemas
# ---------------------------------------------------------------------------

class BitcoinTransactionCreate(BaseModel):
    """Schema for recording a new Bitcoin transaction."""
    campaign_id: Optional[UUID] = None
    tx_type: str = Field(..., max_length=50)  # TransactionType value
    amount_sats: int = Field(..., ge=0)
    fee_sats: int = Field(0, ge=0)
    payment_hash: Optional[str] = Field(None, max_length=200)
    payment_request: Optional[str] = Field(None, max_length=2_000)
    txid: Optional[str] = Field(None, max_length=200)
    address: Optional[str] = Field(None, max_length=200)
    description: Optional[str] = Field(None, max_length=5_000)
    agent_tool_execution_id: Optional[UUID] = None
    approval_id: Optional[UUID] = None


class BitcoinTransactionResponse(BaseModel):
    """Schema for Bitcoin transaction response."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    campaign_id: Optional[UUID]
    user_id: UUID
    tx_type: str
    status: str
    amount_sats: int
    fee_sats: int
    payment_hash: Optional[str]
    payment_request: Optional[str]
    txid: Optional[str]
    address: Optional[str]
    description: Optional[str]
    agent_tool_execution_id: Optional[UUID]
    approval_id: Optional[UUID]
    created_at: datetime
    confirmed_at: Optional[datetime]


# ---------------------------------------------------------------------------
# Spend Approval schemas
# ---------------------------------------------------------------------------

class SpendApprovalCreate(BaseModel):
    """Schema for creating a spend approval request."""
    campaign_id: Optional[UUID] = None
    trigger: str = Field(..., max_length=50)  # SpendTrigger value
    amount_sats: int = Field(..., gt=0)
    fee_estimate_sats: int = Field(0, ge=0)
    payment_request: Optional[str] = Field(None, max_length=2_000)
    destination_address: Optional[str] = Field(None, max_length=200)
    description: str = Field(..., min_length=1, max_length=5_000)
    budget_context: dict = Field(default_factory=dict)


class SpendApprovalReview(BaseModel):
    """Schema for reviewing (approving/rejecting) a spend approval."""
    action: str = Field(..., pattern="^(approved|rejected)$")
    review_notes: Optional[str] = Field(None, max_length=5_000)


class SpendApprovalResponse(BaseModel):
    """Schema for spend approval response."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    campaign_id: Optional[UUID]
    requested_by_id: UUID
    trigger: str
    status: str
    amount_sats: int
    fee_estimate_sats: int
    payment_request: Optional[str]
    destination_address: Optional[str]
    description: str
    budget_context: dict
    reviewed_by_id: Optional[UUID]
    reviewed_at: Optional[datetime]
    review_notes: Optional[str]
    advisor_conversation_id: Optional[UUID]
    created_at: datetime
    expires_at: Optional[datetime]


# ---------------------------------------------------------------------------
# Budget summary / dashboard schemas
# ---------------------------------------------------------------------------

class CampaignBitcoinBudget(BaseModel):
    """Bitcoin budget summary for a single campaign."""
    campaign_id: UUID
    campaign_title: Optional[str] = None
    campaign_status: Optional[str] = None
    bitcoin_budget_sats: Optional[int] = None
    bitcoin_spent_sats: int = 0
    bitcoin_received_sats: int = 0
    bitcoin_remaining_sats: Optional[int] = None  # None if no budget set
    pending_approvals: int = 0
    recent_transactions: list[BitcoinTransactionResponse] = []


class GlobalBitcoinBudget(BaseModel):
    """Global Bitcoin budget rollup across all campaigns."""
    total_budget_sats: int = 0        # Sum of all campaign budgets
    total_spent_sats: int = 0          # Sum of all confirmed sends
    total_received_sats: int = 0       # Sum of all confirmed receives
    total_remaining_sats: int = 0      # budget - spent (only campaigns with budgets)
    total_pending_sats: int = 0        # Sum of pending (unconfirmed) sends
    global_limit_sats: int = 0         # LND_MAX_PAYMENT_SATS setting
    campaigns_with_budget: int = 0     # How many campaigns have BTC budget
    campaigns_over_budget: int = 0     # How many are over budget
    campaigns_near_budget: int = 0     # How many are ≥80% spent (but not over)
    pending_approvals: int = 0         # Total pending spend approvals
    wallet_balance_sats: Optional[int] = None  # LND wallet balance (if connected)
    campaigns: list[CampaignBitcoinBudget] = []
