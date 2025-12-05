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


def _clean_structured_response_text(content_str: str) -> str:
	"""Clean up structured response format strings like 'Returning structured response: text="..."'"""
	if "Returning structured response:" not in content_str:
		return content_str.strip()
	
	# Try to extract text from format: Returning structured response: text="<content>"
	match = re.search(r'text=["\'](.*?)["\']', content_str)
	if match:
		return match.group(1).strip()
	
	# Try alternative format
	match = re.search(r'text=(.+?)(?:\s|$)', content_str)
	if match:
		return match.group(1).strip('"\'').strip()
	
	return content_str.strip()


def _extract_text_from_structured_response(result: Any) -> Optional[str]:
	"""Extract text from structured_response field if it exists."""
	try:
		if not isinstance(result, dict):
			return None
		
		structured = result.get("structured_response")
		if not structured:
			return None
		
		if hasattr(structured, "text"):
			text = structured.text
			if isinstance(text, str):
				return text.strip()
		elif isinstance(structured, dict) and "text" in structured:
			text = structured["text"]
			if isinstance(text, str):
				return text.strip()
	except Exception:
		pass
	
	return None


def _extract_text_from_messages(result: Any) -> Optional[str]:
	"""Extract text from the last message in the messages list (fallback)."""
	try:
		if not isinstance(result, dict):
			return None
		
		messages_result = result.get("messages")
		if not messages_result:
			return None
		
		last = messages_result[-1]
		content = getattr(last, "content", None) or (last.get("content") if isinstance(last, dict) else None)
		
		if content:
			content_str = str(content)
			return _clean_structured_response_text(content_str)
	except Exception:
		pass
	
	return None


def _extract_text_from_content(result: Any) -> str:
	"""Final fallback: extract text from result.content or convert result to string."""
	content = getattr(result, "content", None)
	
	if content:
		content_str = str(content)
		return _clean_structured_response_text(content_str)
	
	# Last resort - convert to string
	result_str = str(result)
	return _clean_structured_response_text(result_str)


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
	agent = create_basic_agent(tools, custom_instructions=context.custom_instructions)
	
	# Prepare messages
	messages = list(history or [])
	messages.append({"role": "user", "content": question})
	
	# Invoke agent
	result = agent.invoke({"messages": messages})
	logger.info(f"Agent invoked successfully")
	
	# Extract token usage
	input_tokens, output_tokens = _extract_token_usage_from_result(result)
	
	# Extract reply text (try multiple methods in order of preference)
	reply_text = (
		_extract_text_from_structured_response(result) or
		_extract_text_from_messages(result) or
		_extract_text_from_content(result)
	)
	
	# Track token usage if project_id provided
	if project_id and reply_text and input_tokens > 0:
		_track_token_usage(project_id, question, reply_text, input_tokens, output_tokens)
	
	return reply_text if reply_text else ""