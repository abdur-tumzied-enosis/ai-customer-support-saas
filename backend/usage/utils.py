"""
Utilities for tracking token usage across the application.
"""
import logging
from decimal import Decimal
from typing import Optional
from django.db import transaction
from django.utils import timezone
from .models import UsageEvent, UsageCounter

logger = logging.getLogger(__name__)


def _consistent_message_id(conversation_id, message_id):
    """Drop message_id (warn) when that message belongs to another conversation."""
    if conversation_id and message_id:
        from channels_app.models import Message

        owner = Message.objects.filter(id=message_id).values_list("conversation_id", flat=True).first()
        if owner is not None and str(owner) != str(conversation_id):
            logger.warning(f"Usage message {message_id} is not in conversation {conversation_id}; dropping message link")
            return None
    return message_id


def track_token_usage(
    project_id: str,
    kind: str,
    tokens: int,
    metadata: Optional[dict] = None,
    cost_usd: Optional[Decimal] = None,
    conversation_id: Optional[str] = None,
    message_id: Optional[str] = None,
) -> None:
    """
    Track token usage for a project.

    Args:
        project_id: UUID of the project
        kind: Type of usage - "chat_in", "chat_out", or "embed"
        tokens: Number of tokens used
        metadata: Optional additional metadata
        cost_usd: USD cost of these tokens, None when unknown (see llm_costs)
    """
    try:
        if tokens <= 0:
            return

        message_id = _consistent_message_id(conversation_id, message_id)
        UsageEvent.objects.create(
            project_id=project_id,
            kind=kind,
            tokens=tokens,
            cost_usd=cost_usd,
            conversation_id=conversation_id,
            message_id=message_id,
            metadata=metadata or {}
        )
        logger.info(f"Tracked {tokens} tokens for project {project_id}, kind={kind}")
    except Exception as e:
        # Don't fail the main operation if usage tracking fails
        logger.error(f"Failed to track token usage: {e}", exc_info=True)


def llm_costs(messages) -> tuple[Optional[Decimal], Optional[Decimal]]:
    """
    USD (input, output) cost of these AIMessages; (None, None) when any message's cost is unknown. Never raises.

    OpenRouter reports the amount it charged (response_metadata["cost"]) as one total, so it is booked on output.
    # ponytail: no in/out split for OpenRouter; sum both kinds for spend. Split via AIModel prices if per-kind cost matters.
    Other providers (Google) are priced from the AIModel whose name matches the response's model_name.
    """
    from agents.models import AIModel

    try:
        cost_in = cost_out = Decimal(0)
        for message in messages:
            meta, usage = message.response_metadata or {}, message.usage_metadata or {}
            if meta.get("cost") is not None:
                cost_out += Decimal(str(meta["cost"]))
                continue
            model = AIModel.objects.filter(
                name=meta.get("model_name"), input_price_per_mtok__isnull=False, output_price_per_mtok__isnull=False
            ).first()
            if not model:
                return None, None
            cost_in += usage.get("input_tokens", 0) * model.input_price_per_mtok / 1_000_000
            cost_out += usage.get("output_tokens", 0) * model.output_price_per_mtok / 1_000_000
        return cost_in, cost_out
    except Exception as e:
        logger.warning(f"Failed to price LLM usage: {e}")
        return None, None


def record_image_usage(
    project_id: str, op: str, *, conversation_id: Optional[str] = None, message_id: Optional[str] = None, **metadata
) -> None:
    """Record one photo operation (op: "index" or "search"). Self-hosted, so cost is $0. Never raises."""
    try:
        # atomic: a failed insert must not poison a caller's open transaction
        with transaction.atomic():
            UsageEvent.objects.create(
                project_id=project_id, kind="image", tokens=0, cost_usd=Decimal(0), metadata={"op": op, **metadata},
                conversation_id=conversation_id, message_id=_consistent_message_id(conversation_id, message_id),
            )
    except Exception as e:
        logger.error(f"Failed to record image usage: {e}", exc_info=True)


def estimate_tokens(text: str) -> int:
    """
    Estimate the number of tokens in a text string.
    This is a rough approximation: ~1 token per 4 characters for English.
    
    Args:
        text: The text to estimate tokens for
        
    Returns:
        Estimated number of tokens
    """
    # Simple estimation: 1 token ≈ 4 characters
    return max(1, len(text) // 4)

