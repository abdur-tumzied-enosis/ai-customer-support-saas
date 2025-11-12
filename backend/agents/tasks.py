from celery import shared_task


@shared_task
def generate_and_send_reply(message_id: int) -> None:
	# Implemented in later tasks: retrieve message, run RAG chain, send via Facebook Send API
	return None


