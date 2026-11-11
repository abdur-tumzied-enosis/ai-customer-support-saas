"""
Utilities for tracking token usage across the application.
"""
import logging
from decimal import Decimal
from typing import Literal, Optional
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
    pricing: Optional[dict] = None,
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
        pricing: Snapshot from llm_pricing (or a system_default dict); keys cost_usd, cost_source,
            provider, model_name, price_in_per_mtok, price_out_per_mtok, optional cost_note (stored in metadata)
    """
    try:
        if tokens <= 0:
            return

        pricing = pricing or {}
        metadata = dict(metadata or {})
        if pricing.get("cost_note"):
            metadata["cost_note"] = pricing["cost_note"]
        message_id = _consistent_message_id(conversation_id, message_id)
        UsageEvent.objects.create(
            project_id=project_id,
            kind=kind,
            tokens=tokens,
            cost_usd=pricing.get("cost_usd"),
            cost_source=pricing.get("cost_source", ""),
            provider=pricing.get("provider", ""),
            model_name=pricing.get("model_name", ""),
            price_in_per_mtok=pricing.get("price_in_per_mtok"),
            price_out_per_mtok=pricing.get("price_out_per_mtok"),
            conversation_id=conversation_id,
            message_id=message_id,
            metadata=metadata,
        )
        logger.info(f"Tracked {tokens} tokens for project {project_id}, kind={kind}")
    except Exception as e:
        # Don't fail the main operation if usage tracking fails
        logger.error(f"Failed to track token usage: {e}", exc_info=True)


def _unknown_snapshot() -> dict:
    return {"cost_usd": None, "cost_source": "unknown", "provider": "", "model_name": "", "price_in_per_mtok": None, "price_out_per_mtok": None}


def _provider_of(meta: dict, name: str) -> str:
    """OpenRouter reports a `cost` and uses "vendor/model" ids; Google reports neither."""
    return "openrouter" if "cost" in meta or "/" in name else "google"


def llm_pricing(messages) -> dict:
    """
    Pricing snapshot for the chat_in and chat_out events of one LLM call: {"chat_in": snap, "chat_out": snap}.
    Never raises. Unknown cost is None, never 0.

    OpenRouter reports the amount it charged (response_metadata["cost"]); it is booked on chat_out, chat_in gets 0.
    Other messages are priced from the AIModel whose provider and name match the response.
    Rates are stored only when all messages came from one model; a mixed call stores no rates.
    """
    from agents.models import AIModel

    try:
        cost_in = cost_out = Decimal(0)
        sources, providers, names, unknown = set(), set(), [], False
        for message in messages:
            meta, usage = message.response_metadata or {}, message.usage_metadata or {}
            name = meta.get("model_name") or ""
            names.append(name)
            provider = _provider_of(meta, name)
            providers.add(provider)
            if meta.get("cost") is not None:
                cost_out += Decimal(str(meta["cost"]))
                sources.add("provider_reported")
                continue
            model = AIModel.objects.filter(
                provider=provider, name=name, input_price_per_mtok__isnull=False, output_price_per_mtok__isnull=False
            ).first()
            if not model:
                unknown = True
                continue
            cost_in += usage.get("input_tokens", 0) * model.input_price_per_mtok / 1_000_000
            cost_out += usage.get("output_tokens", 0) * model.output_price_per_mtok / 1_000_000
            sources.add("token_calculated")

        unique = list(dict.fromkeys(names))
        provider_field = ",".join(sorted(providers))
        model_name = unique[0] if len(unique) == 1 else ",".join(unique)
        if not names or unknown:  # unknown events still record which model they came from
            unknown_snap = {**_unknown_snapshot(), "provider": provider_field, "model_name": model_name}
            return {"chat_in": dict(unknown_snap), "chat_out": dict(unknown_snap)}

        rate_in = rate_out = None
        if len(unique) == 1 and len(providers) == 1:
            rates = AIModel.objects.filter(
                provider=provider_field, name=unique[0], input_price_per_mtok__isnull=False, output_price_per_mtok__isnull=False
            ).first()
            if rates:
                rate_in, rate_out = rates.input_price_per_mtok, rates.output_price_per_mtok

        base = {"provider": provider_field, "model_name": model_name, "price_in_per_mtok": rate_in, "price_out_per_mtok": rate_out}
        if "token_calculated" in sources:
            return {
                "chat_in": {**base, "cost_usd": cost_in, "cost_source": "token_calculated"},
                "chat_out": {**base, "cost_usd": cost_out, "cost_source": "token_calculated"},
            }
        return {
            "chat_in": {**base, "cost_usd": Decimal(0), "cost_source": "provider_reported", "cost_note": "cost booked on chat_out"},
            "chat_out": {**base, "cost_usd": cost_out, "cost_source": "provider_reported"},
        }
    except Exception as e:
        logger.warning(f"Failed to price LLM usage: {e}")
        return {"chat_in": _unknown_snapshot(), "chat_out": _unknown_snapshot()}


def llm_costs(messages) -> tuple[Optional[Decimal], Optional[Decimal]]:
    """USD (input, output) cost of these AIMessages; (None, None) when any message's cost is unknown. Never raises."""
    pricing = llm_pricing(messages)
    return pricing["chat_in"]["cost_usd"], pricing["chat_out"]["cost_usd"]


def record_model_usage(
    project_id: str,
    kind: Literal["embed", "image", "rerank"],
    *,
    tokens: int,
    cost_usd: Optional[Decimal],
    model: str,
    image_tokens: int = 0,
    conversation_id: Optional[str] = None,
    message_id: Optional[str] = None,
    **metadata,
) -> None:
    """Record one embed/image/rerank call. Reported cost wins, else tokens x AIModel price, else unknown (None). Never raises."""
    from agents.models import AIModel

    try:
        provider = _provider_of({}, model)
        row = AIModel.objects.filter(provider=provider, name=model, input_price_per_mtok__isnull=False).first()
        rate_in = row.input_price_per_mtok if row else None
        if cost_usd is not None:
            source = "provider_reported"
        elif kind == "image":  # image events are priced on image tokens only, never also at the text rate
            if row and row.image_price_per_mtok is not None and image_tokens:
                cost_usd, source = image_tokens * row.image_price_per_mtok / 1_000_000, "token_calculated"
            else:
                source = "unknown"
        elif row and tokens:  # no usage reported (tokens == 0) is unknown, never $0
            cost_usd, source = tokens * row.input_price_per_mtok / 1_000_000, "token_calculated"
        else:
            source = "unknown"
        # atomic: a failed insert must not poison a caller's open transaction
        with transaction.atomic():
            UsageEvent.objects.create(
                project_id=project_id, kind=kind, tokens=tokens, cost_usd=cost_usd, cost_source=source,
                provider=provider, model_name=model, price_in_per_mtok=rate_in, metadata=metadata,
                conversation_id=conversation_id, message_id=_consistent_message_id(conversation_id, message_id),
            )
    except Exception as e:
        logger.error(f"Failed to record {kind} usage: {e}", exc_info=True)


def record_image_usage(
    project_id: str,
    op: str,
    *,
    tokens: int,
    cost_usd: Optional[Decimal],
    model: str = "cohere/embed-5-fast",
    conversation_id: Optional[str] = None,
    message_id: Optional[str] = None,
    **metadata,
) -> None:
    """Record one photo operation (op: "index" or "search"). Never raises."""
    record_model_usage(
        # Embedded reports only prompt tokens; a photo request is image-only, so all of them are image tokens
        project_id, "image", tokens=tokens, cost_usd=cost_usd, model=model, image_tokens=tokens,
        conversation_id=conversation_id, message_id=message_id, op=op, **metadata,
    )


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
