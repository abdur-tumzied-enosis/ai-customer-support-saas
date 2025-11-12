# import os
# from unittest import skipUnless
# from django.test import TestCase
# from accounts.models import Business
# from agents.rag import generate_reply


# @skipUnless(os.getenv("GOOGLE_API_KEY"), "GOOGLE_API_KEY not set; skipping interactive test.")
# class RagInteractiveTest(TestCase):
# 	def test_generate_reply_interactive(self):
# 		question = os.getenv("INTERACTIVE_QUESTION", "What are your hours?")
# 		business_name = os.getenv("INTERACTIVE_BUSINESS_NAME", "Test Co")
# 		business_slug = os.getenv("INTERACTIVE_BUSINESS_SLUG", "test-co")

# 		business = Business.objects.create(name=business_name, slug=business_slug)
# 		reply = generate_reply(question, business)

# 		print("\n--- Interactive RAG Test ---")
# 		print(f"Business: {business.name} ({business.slug})")
# 		print(f"Q: {question}")
# 		print(f"A: {reply}\n")

# 		self.assertIsInstance(reply, str)