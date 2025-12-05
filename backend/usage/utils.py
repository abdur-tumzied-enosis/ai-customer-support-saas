"""
Utilities for tracking token usage across the application.
"""
import logging
from typing import Optional
from django.utils import timezone
from .models import UsageEvent, UsageCounter

logger = logging.getLogger(__name__)


def track_token_usage(
    project_id: str,
    kind: str,
    tokens: int,
    metadata: Optional[dict] = None
) -> None:
    """
    Track token usage for a project.
    
    Args:
        project_id: UUID of the project
        kind: Type of usage - "chat_in", "chat_out", or "embed"
        tokens: Number of tokens used
        metadata: Optional additional metadata
    """
    try:
        if tokens <= 0:
            return
        
        UsageEvent.objects.create(
            project_id=project_id,
            kind=kind,
            tokens=tokens,
            metadata=metadata or {}
        )
        logger.info(f"Tracked {tokens} tokens for project {project_id}, kind={kind}")
    except Exception as e:
        # Don't fail the main operation if usage tracking fails
        logger.error(f"Failed to track token usage: {e}", exc_info=True)


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

