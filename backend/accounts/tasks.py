from celery import shared_task
from django.core.mail import send_mail
from django.db import transaction


# smtplib errors subclass OSError, so Gmail hiccups and timeouts retry
@shared_task(autoretry_for=(OSError,), retry_backoff=True, max_retries=3)
def send_email(to: str, subject: str, body: str) -> None:
	send_mail(subject, body, None, [to])


def queue_email(to: str, subject: str, body: str) -> None:
	"""Send after the current transaction commits, off the request (SMTP is slow, and timing must not reveal
	whether an account exists)."""
	transaction.on_commit(lambda: send_email.delay(to, subject, body))
