import requests
from urllib3.exceptions import MaxRetryError, NewConnectionError


def never_sent(exc: Exception) -> bool:
	"""True if a requests error happened while connecting, so the server never saw the request and it is safe to retry.
	A read timeout or a connection dropped mid-response is not: the server may have acted on it."""
	if isinstance(exc, requests.ConnectTimeout):
		return True
	# requests wraps a refused connection or failed DNS lookup as ConnectionError(MaxRetryError(reason=NewConnectionError))
	return isinstance(exc, requests.ConnectionError) and any(
		isinstance(a, MaxRetryError) and isinstance(a.reason, NewConnectionError) for a in exc.args
	)
