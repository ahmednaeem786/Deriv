"""Data contracts, schema definitions, and validation models."""

from pydantic import BaseModel, Field


# Input Schemas
class RawTicket(BaseModel):
    """Schema for individual ticket objects parsed from tickets.json."""

    ticket_id: str
    customer_id: str
    subject: str
    message: str
    channel: str
    created_at: str


class ReplyStyleConfig(BaseModel):
    """Configuration for LLM-generated suggested reply style."""

    tone: str
    max_words: int


class TriageConfig(BaseModel):
    """Schema for triage_config.json."""

    allowed_categories: list[str]
    allowed_priorities: list[str]
    reply_style: ReplyStyleConfig
    routing_rules: dict[str, str]


# Stage 1: Deterministic Normalization
class NormalizedTicket(BaseModel):
    """Schema for entries stored in normalized_tickets.json."""

    ticket_id: str
    subject: str
    message: str
    channel: str
    created_at: str
    text_for_model: str
    char_count: int


# Stage 2: LLM Predictions & Structured Outputs
class SinglePrediction(BaseModel):
    """Schema for a single ticket's LLM prediction."""

    ticket_id: str
    category: str
    priority: str
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Confidence score for category and priority classification (0.0 to 1.0)",
    )
    reason: str
    suggested_reply: str
    route_to: str


class BatchTriageResponse(BaseModel):
    """Container schema for the single batch LLM call output."""

    tickets: list[SinglePrediction]


# Stage 3: Human Review Checkpoint Overrides
class ReviewOverride(BaseModel):
    """Schema for records in review_overrides.json."""

    ticket_id: str
    old_category: str
    new_category: str
    old_priority: str
    new_priority: str


# Stage 4: Final Queue Artifact
class FinalQueueItem(BaseModel):
    """Schema for items in final_queue.json."""

    ticket_id: str
    final_category: str
    final_priority: str
    final_route_to: str
    suggested_reply: str
    was_overridden: bool


# Stage 5: Escalation Item Schema
class EscalationItem(BaseModel):
    """Schema for records in escalations.json."""

    ticket_id: str
    category: str
    confidence: float
    reason: str
