import re
from dataclasses import dataclass
from typing import Callable, Optional, List, Any, Dict, TypedDict, Literal, Sequence
from .llm import create_basic_agent
import logging
logger = logging.getLogger(__name__)

@dataclass
class Context:
	vector_collection: Optional[str] = None
	custom_instructions: Optional[List[dict]] = None  # List of {'label': str, 'message': str}


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
		"""Retrieve relevant knowledge snippets for given a user query."""
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
	# Pass custom instructions to the agent
	agent = create_basic_agent(tools, custom_instructions=context.custom_instructions)
	messages = list(history or [])
	messages.append({"role": "user", "content": question})
	result = agent.invoke({"messages": messages})
	logger.info(f"Result: {result}")

	# Extract text from structured response format
	try:
		# Check if result has structured_response (from response_format)
		if result.get("structured_response"):
			structured = result["structured_response"]
			if hasattr(structured, "text"):
				text = structured.text
				# Clean up if it contains metadata
				if isinstance(text, str):
					return text.strip()
			elif isinstance(structured, dict) and "text" in structured:
				text = structured["text"]
				if isinstance(text, str):
					return text.strip()
	except Exception:
		pass

	# Best-effort extraction of final content (fallback)
	try:
		messages = result.get("messages")  # type: ignore[attr-defined]
		if messages:
			last = messages[-1]
			content = getattr(last, "content", None) or (last.get("content") if isinstance(last, dict) else None)
			if content:
				# Clean up structured response format strings
				content_str = str(content)
				# Remove "Returning structured response: text=" prefix if present
				if "Returning structured response:" in content_str:
					# Extract text from format: Returning structured response: text="<content>"
					match = re.search(r'text=["\'](.*?)["\']', content_str)
					if match:
						return match.group(1).strip()
					# Try to extract from text="..." format
					match = re.search(r'text=(.+?)(?:\s|$)', content_str)
					if match:
						text = match.group(1).strip('"\'')
						return text.strip()
				return content_str.strip()
	except Exception:
		pass

	# Final fallback - clean up string representation
	content = getattr(result, "content", None)
	if content:
		content_str = str(content)
		# Remove structured response metadata if present
		if "Returning structured response:" in content_str:
			match = re.search(r'text=["\'](.*?)["\']', content_str)
			if match:
				return match.group(1).strip()
		return content_str.strip()
	
	# Last resort - convert to string and clean
	result_str = str(result)
	if "Returning structured response:" in result_str:
		match = re.search(r'text=["\'](.*?)["\']', result_str)
		if match:
			return match.group(1).strip()
	
	return result_str.strip()