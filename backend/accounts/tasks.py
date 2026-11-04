from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils.html import escape, linebreaks, urlize

FONT = "Roboto,Helvetica,Arial,sans-serif"

# ponytail: one inline-styled wrapper for every email; move to a Django template once emails need per-type layouts.
# Header is the hosted logo PNG (EMAIL_LOGO_URL; clients don't render SVG), else a text wordmark like the logo.
WORDMARK = '<strong>Tumar</strong>Agent'
LOGO = '<img src="{url}" alt="TumarAgent" height="32" style="display:block;height:32px;width:auto;border:0">'
HTML = f"""<!doctype html>
<html><body style="margin:0;padding:24px 12px;background:#f4f4f5;font-family:{FONT};color:#18181b">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:480px;background:#ffffff;border:1px solid #e4e4e7;border-radius:8px">
<tr><td style="padding:20px 28px;border-bottom:1px solid #e4e4e7;font-size:20px;letter-spacing:-0.2px">
<a href="https://tumaragent.com" style="color:#000000;text-decoration:none">{{header}}</a></td></tr>
<tr><td style="padding:24px 28px;font-size:15px;line-height:1.6">{{body}}<p style="margin:24px 0 0">— The {{name}} team</p></td></tr>
</table>
<p style="margin:16px 0 0;font-size:12px;color:#71717a">{{name}} · <a href="https://tumaragent.com" style="color:#71717a">tumaragent.com</a></p>
</td></tr></table>
</body></html>"""


# smtplib errors subclass OSError, so Gmail hiccups and timeouts retry
@shared_task(autoretry_for=(OSError,), retry_backoff=True, max_retries=3)
def send_email(to: str, subject: str, body: str) -> None:
	name = settings.PRODUCT_NAME
	header = LOGO.format(url=escape(settings.EMAIL_LOGO_URL)) if settings.EMAIL_LOGO_URL else WORDMARK
	html = HTML.format(header=header, body=linebreaks(urlize(body, autoescape=True)), name=name)
	send_mail(subject, f"{body}\n\n— The {name} team\nhttps://tumaragent.com", None, [to], html_message=html)


def queue_email(to: str, subject: str, body: str) -> None:
	"""Send after the current transaction commits, off the request (SMTP is slow, and timing must not reveal
	whether an account exists)."""
	transaction.on_commit(lambda: send_email.delay(to, subject, body))
