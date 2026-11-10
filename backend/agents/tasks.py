import logging

from celery import shared_task

from . import pricing

logger = logging.getLogger(__name__)


@shared_task
def generate_and_send_reply(message_id: int) -> None:
	# Implemented in later tasks: retrieve message, run RAG chain, send via Facebook Send API
	return None


@shared_task
def sync_openrouter_prices() -> dict:
	result = pricing.sync_openrouter_prices()
	logger.info("OpenRouter price sync: %s", result)
	return result
