import time
from dataclasses import dataclass, field
from typing import Callable, Optional, List, Any, Dict, TypedDict, Literal, Sequence
from langchain_core.messages import AIMessage
from .llm import create_basic_agent
from .vector_store import rerank_docs
from .api_tool import build_call_api_tool
import logging
logger = logging.getLogger(__name__)

@dataclass
class Context:
	knowledge_base_id: Optional[str] = None
	vector_collection: Optional[str] = None
	custom_instructions: Optional[List[dict]] = None  # List of {'label': str, 'message': str}
	memory_prompt: str = ""  # what we remember about the customer (channels_app.memory)
	api_domains: Optional[List[dict]] = None  # enables call_api tool when non-empty
	api_call_log: List[dict] = field(default_factory=list)  # filled by call_api during the reply
	handoff_reason: Optional[str] = None  # set by escalate_to_human during the reply
	trace: Dict[str, Any] = field(default_factory=dict)  # model/ms/tokens/tool_calls/sources, stored on the reply message


@dataclass
class ToolDeps:
	"""
	External dependencies that allow this module to remain framework-agnostic.
	- retriever_factory: given a collection name, returns an object with get_relevant_documents(str) -> List[doc]
	- upload_document: given a URI, uploads/enqueues ingestion and returns a status string
	"""
	retriever_factory: Optional[Callable[[str], Any]] = None
	upload_document: Optional[Callable[[str], str]] = None
	retrieved_factory_for_crawler_url_context: Optional[Callable] = None


def _source(doc) -> str:
	meta = doc.metadata or {}
	return meta.get("page_url") or meta.get("document_filename") or meta.get("document_id", "")


def _format_docs(docs: list) -> str:
	"""Format retrieved docs with source metadata for better agent attribution."""
	if not docs:
		return "No relevant information found."
	parts = []
	for doc in docs:
		meta = doc.metadata or {}
		source = _source(doc)
		title = meta.get("page_title", "")
		header = f"[Source: {title} — {source}]" if title else (f"[Source: {source}]" if source else "")
		parts.append(f"{header}\n{doc.page_content}".strip())
	return "\n\n---\n\n".join(parts)


def _build_tools_from_context(context: Context, deps: ToolDeps):

	tools: List[Callable[..., str]] = []

	retriever = None

	if context.vector_collection and deps.retriever_factory:
		try:
			retriever = deps.retriever_factory(context.vector_collection)
		except Exception:
			retriever = None

	def retrieve_business_context(query: str) -> str:
		"""Search the business knowledge base for information relevant to the query.
		Use this tool to find answers about products, services, policies, FAQs, pricing,
		and any other information the business has uploaded. Call this whenever the user
		asks something that may be covered by business documents. Use a focused keyword
		query, not the full user message."""
		logger.info(f"[Tool: retrieve_business_context] query={query!r}")
		if not retriever:
			return "No relevant information found."
		docs = retriever.invoke(query)
		docs = rerank_docs(query, docs, top_k=4)
		context.trace.setdefault("sources", []).extend(_source(d) for d in docs)
		result = _format_docs(docs)
		logger.info(f"[Tool: retrieve_business_context] returned {len(docs)} docs")
		return result

	tools.append(retrieve_business_context)

	if deps.retrieved_factory_for_crawler_url_context:
		try:
			web_retriever = deps.retrieved_factory_for_crawler_url_context()

			def retrieve_crawler_context(query: str) -> str:
				"""Search web pages that have been crawled and indexed from the business's URLs.
				Use this tool to find information from the company's website, blog, landing pages,
				or any other URLs that have been added as web sources. Useful for detailed product
				info, documentation, or anything that may live on a webpage rather than an uploaded
				file. Use a focused keyword query, not the full user message."""
				logger.info(f"[Tool: retrieve_crawler_context] query={query!r}")
				if not web_retriever:
					return "No relevant information found."
				docs = web_retriever.invoke(query)
				docs = rerank_docs(query, docs, top_k=4)
				context.trace.setdefault("sources", []).extend(_source(d) for d in docs)
				result = _format_docs(docs)
				logger.info(f"[Tool: retrieve_crawler_context] returned {len(docs)} docs")
				return result

			tools.append(retrieve_crawler_context)
		except Exception:
			pass

	def escalate_to_human(reason: str) -> str:
		"""Hand this conversation to a human staff member. Use it when the customer asks for a person,
		is angry or abusive, raises a refund, complaint, cancellation or legal matter, when you could not
		answer their question twice in a row, or when the custom instructions say so. Give a short reason."""
		context.handoff_reason = (reason or "").strip() or "customer needs a person"
		logger.info(f"[Tool: escalate_to_human] reason={context.handoff_reason!r}")
		return "Handed off. Tell the customer a person will follow up."

	tools.append(escalate_to_human)

	if context.api_domains:
		tools.append(build_call_api_tool(context.api_domains, context.api_call_log))

	return tools

class ChatMessage(TypedDict):
	role: Literal["user", "assistant"]
	content: str


def _extract_token_usage_from_result(result: Any) -> tuple[int, int]:
	"""
	Extract and sum token usage from all AIMessages in the result.
	Returns (total_input_tokens, total_output_tokens).
	"""
	total_input_tokens = 0
	total_output_tokens = 0
	
	try:
		if isinstance(result, dict):
			messages_result = result.get("messages", [])
			if messages_result:
				for message in messages_result:
					msg_usage = None
					if hasattr(message, "usage_metadata"):
						msg_usage = message.usage_metadata
					elif isinstance(message, dict) and "usage_metadata" in message:
						msg_usage = message["usage_metadata"]
					
					if msg_usage:
						if isinstance(msg_usage, dict):
							total_input_tokens += msg_usage.get('input_tokens', 0)
							total_output_tokens += msg_usage.get('output_tokens', 0)
						else:
							total_input_tokens += getattr(msg_usage, 'input_tokens', 0)
							total_output_tokens += getattr(msg_usage, 'output_tokens', 0)
		
		if total_input_tokens > 0 or total_output_tokens > 0:
			logger.info(f"Token usage - Input: {total_input_tokens}, Output: {total_output_tokens}")
	except Exception as e:
		logger.warning(f"Failed to extract usage metadata: {e}")
	
	return total_input_tokens, total_output_tokens


def _record_trace(trace: dict, new_messages: list) -> None:
	"""Model, token totals and tool calls of this run (history messages excluded)."""
	ai = [m for m in new_messages if isinstance(m, AIMessage)]
	if ai:
		trace["model"] = ai[-1].response_metadata.get("model_name")
	trace["tokens_in"] = sum((m.usage_metadata or {}).get("input_tokens", 0) for m in ai)
	trace["tokens_out"] = sum((m.usage_metadata or {}).get("output_tokens", 0) for m in ai)
	trace["tool_calls"] = [{"name": c["name"], "args": c["args"]} for m in ai for c in m.tool_calls]


def _extract_reply_text(result: Any) -> str:
	"""Text of the agent's final message."""
	messages_result = result.get("messages") if isinstance(result, dict) else None
	if not messages_result:
		return ""
	content = getattr(messages_result[-1], "content", "")
	if isinstance(content, list):  # some providers (Gemini) return content blocks
		content = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
	return str(content).strip()


def _track_token_usage(
	project_id: str, 
	question: str, 
	reply_text: str, 
	input_tokens: int, 
	output_tokens: int
) -> None:
	"""Track token usage in the database."""
	try:
		from usage.utils import track_token_usage
		
		if input_tokens > 0:
			track_token_usage(project_id, "chat_in", input_tokens, {
				"question_length": len(question),
				"source": "langchain_usage_metadata"
			})
		
		if output_tokens > 0:
			track_token_usage(project_id, "chat_out", output_tokens, {
				"response_length": len(reply_text),
				"source": "langchain_usage_metadata"
			})
	except Exception as e:
		logger.warning(f"Failed to track token usage: {e}")


def generate_reply(
	question: str, 
	context: Context, 
	deps: Optional[ToolDeps] = None, 
	history: Optional[Sequence[ChatMessage]] = None, 
	project_id: Optional[str] = None
) -> str:
	"""
	Generate a reply to a user question using RAG (Retrieval Augmented Generation).
	
	Args:
		question: User's question/message
		context: Context with vector collection and custom instructions
		deps: Optional tool dependencies (retriever, upload functions)
		history: Optional conversation history
		project_id: Optional project ID for token tracking
	
	Returns:
		Generated reply text
	"""
	# Setup
	deps = deps or ToolDeps()
	tools = _build_tools_from_context(context, deps)
	agent = create_basic_agent(tools, custom_instructions=context.custom_instructions, memory=context.memory_prompt)
	
	# Prepare messages
	messages = list(history or [])
	messages.append({"role": "user", "content": question})
	
	# Invoke agent
	start = time.monotonic()
	try:
		result = agent.invoke({"messages": messages})
	finally:
		context.trace["ms"] = int((time.monotonic() - start) * 1000)
	logger.info(f"Agent invoked successfully")
	_record_trace(context.trace, result["messages"][len(messages):])
	
	# Extract token usage
	input_tokens, output_tokens = _extract_token_usage_from_result(result)
	
	reply_text = _extract_reply_text(result)

	# Track token usage if project_id provided
	if project_id and reply_text and input_tokens > 0:
		_track_token_usage(project_id, question, reply_text, input_tokens, output_tokens)

	return reply_text