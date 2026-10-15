import re
from typing import Optional
import requests

import logging
logger = logging.getLogger(__name__)

FACEBOOK_API_URL = "https://graph.facebook.com/v21.0/me/messages"
MAX_TEXT_CHARS = 1900  # Messenger rejects text over 2000; margin for the cleanup in send_text_message
INSTAGRAM_MAX_TEXT_BYTES = 990  # Instagram rejects text over 1000 UTF-8 bytes (Bengali/emoji: 3-4 bytes per char)


def split_text(text: str, limit: int = MAX_TEXT_CHARS, max_bytes: Optional[int] = None) -> list:
	"""Split long text into parts of at most `limit` chars (and `max_bytes` UTF-8 bytes), on paragraph,
	line, sentence or word breaks."""
	parts = []
	while text:
		window = text[:limit]
		if max_bytes:
			window = window.encode()[:max_bytes].decode(errors="ignore")  # drops a char cut mid-sequence
		if len(window) == len(text):
			break
		# Only break past the halfway point, so a stray early newline doesn't leave a tiny part
		size = len(window)
		cut = next((i + len(sep) for sep in ("\n\n", "\n", ". ", " ") if (i := window.rfind(sep)) > size // 2), size)
		parts.append(text[:cut].strip())
		text = text[cut:]
	return [p for p in parts + [text.strip()] if p]


def send_text_message(page_access_token: str, recipient_psid: str, text: str) -> dict:
	"""
	Send a text message via Facebook Messenger API.
	
	Args:
		page_access_token: Facebook page access token
		recipient_psid: Recipient's Facebook PSID (Page-Scoped ID)
		text: Message text content
		
	Returns:
		API response dictionary with keys:
		- recipient_id: The recipient's PSID
		- message_id: The Facebook message ID (e.g., 'xbrIf3dYGmS0oq4ky1AIbh7BgMqGQ')
		Empty dict {} on failure
	"""
	# Ensure text is UTF-8 encoded string
	if not isinstance(text, str):
		text = str(text)
	
	# Encode to UTF-8 and decode back to ensure valid UTF-8
	# This removes any invalid characters
	try:
		text = text.encode('utf-8', errors='replace').decode('utf-8')
	except (UnicodeEncodeError, UnicodeDecodeError) as e:
		logger.error(f"Text encoding error: {e}")
		# Fallback: replace invalid characters
		text = text.encode('utf-8', errors='replace').decode('utf-8')
	
	# Remove or replace any control characters that might cause issues
	# Keep only printable characters and common whitespace
	text = re.sub(r'[\x00-\x08\x0B-\x0C\x0E-\x1F\x7F-\x9F]', '', text)
	
	# Ensure text is not empty after cleaning
	if not text.strip():
		logger.error("Text is empty after encoding/cleaning")
		return {}
	
	url = FACEBOOK_API_URL
	headers = {
		"Authorization": f"Bearer {page_access_token}",
		"Content-Type": "application/json; charset=utf-8",
	}
	payload = {
		"recipient": {"id": recipient_psid},
		"message": {"text": text},
	}
	
	try:
		resp = requests.post(url, headers=headers, json=payload, timeout=10)
		
		# Check for API errors in response
		if resp.status_code != 200:
			error_data = resp.json() if resp.text else {}
			logger.error(f"Facebook API error: {resp.status_code} - {error_data}")
			return {}
		
		resp.raise_for_status()  # Raise exception for bad status codes
		response_data = resp.json()
		
		# Check if response contains an error
		if "error" in response_data:
			logger.error(f"Facebook API error response: {response_data['error']}")
			return {}
		
		logger.info(f"Facebook API response: {resp.status_code} - {response_data}")
		return response_data
	except requests.exceptions.RequestException as e:
		logger.error(f"Failed to send Facebook message: {e}")
		if hasattr(e, 'response') and e.response is not None:
			try:
				error_data = e.response.json()
				logger.error(f"Error response: {error_data}")
			except:
				logger.error(f"Error response text: {e.response.text}")
		return {}




GRAPH_API_URL = "https://graph.facebook.com/v21.0"


def get_user_name(page_access_token: str, psid: str, fields: str = "name") -> str:
	"""Customer's name for the dashboard (so staff can find them in the Meta inbox). "" on any failure.
	Instagram: pass fields="name,username"; many IG profiles have no name."""
	try:
		resp = requests.get(
			f"{GRAPH_API_URL}/{psid}",
			params={"fields": fields, "access_token": page_access_token},
			timeout=5,
		)
		if resp.status_code == 200:
			data = resp.json()
			return (data.get("name") or data.get("username") or "")[:255]
		logger.warning(f"Facebook name lookup failed: HTTP {resp.status_code}")
	except (requests.RequestException, ValueError) as e:
		logger.warning(f"Facebook name lookup failed: {type(e).__name__}")
	return ""


WHATSAPP_MAX_TEXT_CHARS = 4000  # Cloud API rejects text bodies over 4096 chars


def send_whatsapp_text(access_token: str, phone_number_id: str, to: str, text: str) -> dict:
	"""Send a text via the WhatsApp Cloud API. {"message_id": ...} on success, {"error": <Meta's message>} on failure.
	Free-form text only reaches customers within 24h of their last message (error 131047 otherwise)."""
	try:
		resp = requests.post(
			f"{GRAPH_API_URL}/{phone_number_id}/messages",
			headers={"Authorization": f"Bearer {access_token}"},
			json={"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": text}},
			timeout=10,
		)
		data = resp.json()
	except (requests.RequestException, ValueError) as e:
		logger.error(f"WhatsApp send failed: {type(e).__name__}")
		return {"error": "whatsapp send failed"}
	if resp.status_code == 200 and data.get("messages"):
		return {"message_id": data["messages"][0]["id"]}
	error = data.get("error") or {}
	logger.error(f"WhatsApp API error: {resp.status_code} - {error}")
	return {"error": error.get("message") or "whatsapp send failed"}
