import re

from knowledge.price import BENGALI_DIGITS

_MOBILE = re.compile(r"01[3-9]\d{8}")
_SEPARATORS = re.compile(r"[\s\-()\[\]]")


def normalise_bd_phone(raw: str | None) -> str | None:
	"""01XXXXXXXXX for a Bangladeshi mobile in any common form (+880, 880, Bengali digits), else None."""
	if not raw:
		return None
	digits = _SEPARATORS.sub("", raw.translate(BENGALI_DIGITS))
	for prefix in ("+880", "880"):
		if digits.startswith(prefix):
			digits = "0" + digits[len(prefix):]  # the local form keeps its leading 0
			break
	return digits if _MOBILE.fullmatch(digits) else None
