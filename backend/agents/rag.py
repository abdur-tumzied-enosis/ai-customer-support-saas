from dataclasses import dataclass
from typing import Callable, Optional, List, Any, Dict, TypedDict, Literal, Sequence
from .llm import create_basic_agent


@dataclass
class Context:
	vector_collection: Optional[str] = None


@dataclass
class ToolDeps:
	"""
	External dependencies that allow this module to remain framework-agnostic.
	- retriever_factory: given a collection name, returns an object with get_relevant_documents(str) -> List[doc]
	- upload_document: given a URI, uploads/enqueues ingestion and returns a status string
	"""
	retriever_factory: Optional[Callable[[str], Any]] = None
	upload_document: Optional[Callable[[str], str]] = None


def _build_tools_from_context(context: Context, deps: ToolDeps):
	retriever = None
	if context.vector_collection and deps.retriever_factory:
		try:
			retriever = deps.retriever_factory(context.vector_collection)
		except Exception:
			retriever = None

	def retrieve_business_context(query: str) -> str:
		"""Retrieve relevant knowledge snippets for this business given a user query."""
		if not retriever:
			return ""
		# LangChain v1 retrievers implement Runnable; use invoke()
		docs = retriever.invoke(query)
		return "\n\n".join(d.page_content for d in docs)

	tools: List[Callable[..., str]] = [retrieve_business_context]

	if deps.upload_document:
		def upload_business_document(uri: str) -> str:
			"""Upload a document URI to the business knowledge base and start ingestion."""
			try:
				if deps.upload_document:
					return deps.upload_document(uri)
				else:
					return "error: upload_document is not set"
			except Exception as exc:
				return f"error: {exc}"
		tools.append(upload_business_document)

	return tools

class ChatMessage(TypedDict):
	role: Literal["user", "assistant"]
	content: str


def generate_reply(question: str, context: Context, deps: Optional[ToolDeps] = None, history: Optional[Sequence[ChatMessage]] = None) -> str:
	deps = deps or ToolDeps()
	tools = _build_tools_from_context(context, deps)
	agent = create_basic_agent(tools)
	messages = list(history or [])
	messages.append({"role": "user", "content": question})
	result = agent.invoke({"messages": messages})

	# Best-effort extraction of final content
	try:
		messages = result.get("messages")  # type: ignore[attr-defined]
		if messages:
			last = messages[-1]
			content = getattr(last, "content", None) or (last.get("content") if isinstance(last, dict) else None)
			return content or str(result)
	except Exception:
		pass

	content = getattr(result, "content", None)
	return content or str(result)