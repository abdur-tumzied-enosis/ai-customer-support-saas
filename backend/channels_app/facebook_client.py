import re
import requests

import logging
logger = logging.getLogger(__name__)

FACEBOOK_API_URL = "https://graph.facebook.com/v21.0/me/messages"


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


def get_user_name(page_access_token: str, psid: str) -> str:
	"""Customer's name for the dashboard (so staff can find them in the Meta inbox). "" on any failure."""
	try:
		resp = requests.get(
			f"{GRAPH_API_URL}/{psid}",
			params={"fields": "name", "access_token": page_access_token},
			timeout=5,
		)
		if resp.status_code == 200:
			return (resp.json().get("name") or "")[:255]
		logger.warning(f"Facebook name lookup failed: HTTP {resp.status_code}")
	except (requests.RequestException, ValueError) as e:
		logger.warning(f"Facebook name lookup failed: {type(e).__name__}")
	return ""
