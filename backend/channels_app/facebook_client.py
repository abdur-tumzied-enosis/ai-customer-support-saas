import requests


def send_text_message(page_access_token: str, recipient_psid: str, text: str) -> None:
	url = "https://graph.facebook.com/v17.0/me/messages"
	params = {"access_token": page_access_token}
	payload = {
		"recipient": {"id": recipient_psid},
		"message": {"text": text},
	}
	resp = requests.post(url, params=params, json=payload, timeout=10)
	resp.raise_for_status()


