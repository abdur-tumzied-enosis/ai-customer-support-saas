#!/usr/bin/env python
import os
import sys
import argparse


def main() -> int:
	# Ensure project root (backend) is on path when running directly
	current_file = os.path.abspath(__file__)
	backend_root = os.path.abspath(os.path.join(current_file, "..", "..", ".."))
	if backend_root not in sys.path:
		sys.path.insert(0, backend_root)
	# Prefer running from backend root so 'core' is importable
	try:
		os.chdir(backend_root)
	except Exception:
		pass

	# Bootstrap Django
	os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
	try:
		import django  # type: ignore
		django.setup()
	except Exception as exc:
		print(f"Failed to setup Django: {exc}", file=sys.stderr)
		return 1

	# from accounts.models import Business  # type: ignore
	from agents.rag import generate_reply, Context, ToolDeps  # type: ignore
	from agents.vector_store import get_retriever  # type: ignore
	from agents.vector_store import add_text_to_collection  # type: ignore

	parser = argparse.ArgumentParser(
		description="Interactive RAG runner for a Business. If no question is provided, starts a REPL."
	)
	parser.add_argument("--business-name", default=os.getenv("INTERACTIVE_BUSINESS_NAME", "Demo Biz"))
	parser.add_argument("--business-slug", default=os.getenv("INTERACTIVE_BUSINESS_SLUG", "demo-biz"))
	parser.add_argument("question", nargs="*", help="Optional question to ask once and exit")
	args = parser.parse_args()
	relevent_business_data = (
		"Demo Biz is focused on product demonstrations."
		"It represents the critical moment in the sales cycle where a potential client sees the software or service in action."
		"The goal of this call is to highlight key features, address specific pain points, and showcase the solution's value "
		"proposition to secure the next step in the sales process."
		"The business is located in the United States."
		"Contact information: 123 Main St, Anytown, USA, 12345"
		"co founder is John Doe and the CEO is Jane Bob."
		
	)

	print("should upload relevent business data to the vector store?")
	upload_relevent_business_data = input("y/n: ")
	if upload_relevent_business_data == "y":
		print("uploading relevent business data to the vector store...")
		add_text_to_collection(f"kb_{args.business_slug}", relevent_business_data, metadata={"source": "business_data"})
	else:
		print("relevent business data not uploaded to the vector store.")
	# business, _ = Business.objects.get_or_create(name=args.business_name, slug=args.business_slug)

	context = Context(name=args.business_name, vector_collection=f"kb_{args.business_slug}")
	deps = ToolDeps(retriever_factory=get_retriever)

	# One-shot mode if a question is provided
	if args.question:
		question = " ".join(args.question).strip()
		reply = generate_reply(question, context, deps)
		print(f"\nBusiness: {args.business_name} ({args.business_slug})")
		print(f"Q: {question}")
		print(f"A: {reply}\n")
		return 0

	# REPL mode
	print(f"RAG REPL - Business: {args.business_name} ({args.business_slug})")
	print("Type your question and press Enter. Ctrl+C or empty line to exit.\n")
	while True:
		try:
			question = input("> ").strip()
			if not question:
				break
			reply = generate_reply(question, context, deps)
			print(reply if reply else "(no answer)")
		except (EOFError, KeyboardInterrupt):
			print()
			break
	return 0


if __name__ == "__main__":
	raise SystemExit(main())

