from django.db.models import Q

from knowledge.models import CustomInstruction

ORDER_WORDS = ("order", "অর্ডার", "কিনতে", "কিনব", "purchase")
CONFIRM_WORDS = ("confirm", "address", "ঠিকানা", "phone", "ফোন")
RESOLVE_SCOPES = {"convert": "order", "turn_off": "off"}


def _any_word(words) -> Q:
	q = Q()
	for word in words:
		q |= Q(message__icontains=word)
	return q


def find_order_instructions(project) -> list[CustomInstruction]:
	"""General instructions that look like order taking: order words plus a confirmation or address word,
	or an API-tool instruction that mentions orders."""
	general = CustomInstruction.objects.filter(project=project, scope="general")
	taking = general.filter(_any_word(ORDER_WORDS)).filter(_any_word(CONFIRM_WORDS))
	api_orders = general.filter(api_tool_enabled=True, message__icontains="order")
	return list((taking | api_orders).distinct().order_by("-created_at"))
