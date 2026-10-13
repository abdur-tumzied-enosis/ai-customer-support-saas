import logging
from typing import List, Optional
from django.conf import settings
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openrouter import ChatOpenRouter
from langchain.agents import create_agent
from langchain_core.runnables import Runnable

OPENROUTER_DEFAULT_MODEL = "stepfun/step-3.5-flash:free"

logger = logging.getLogger(__name__)

def get_llm(provider: Optional[str] = None, model: Optional[str] = None, **kwargs):
	"""
	Return an LLM instance.

	Args:
		provider: "google" or "openrouter". Defaults to settings.LLM_PROVIDER.
		model: Model name override. Defaults per provider:
			google     → "gemini-3.8-flash"
			openrouter → "nvidia/nemotron-3-super-120b-a12b:free"
		**kwargs: Extra kwargs forwarded to the underlying LLM class.
	"""
	provider = provider or settings.LLM_PROVIDER
	model = model or settings.LLM_MODEL
	if provider == "openrouter":
		logger.warning("Using OpenRouter LLM - ensure your OPENROUTER_API_KEY is set and monitor usage to avoid unexpected costs.")
		return ChatOpenRouter(
			model=model or OPENROUTER_DEFAULT_MODEL,
			api_key=settings.OPENROUTER_API_KEY,
			**kwargs,
		)
	# default: google
	if settings.GOOGLE_API_KEY:
		# API key (Vertex express mode / Gemini API): project/location would override the key and force ADC
		auth = {"api_key": settings.GOOGLE_API_KEY}
	else:
		# No key: Vertex via service-account / ADC credentials
		auth = {"project": settings.GOOGLE_CLOUD_PROJECT, "location": settings.GOOGLE_CLOUD_LOCATION}
	return ChatGoogleGenerativeAI(
		model=model or "gemini-3.8-flash",
		vertexai=settings.GOOGLE_USE_VERTEXAI,
		**auth,
		**kwargs,
	)


def build_system_prompt(custom_instructions: Optional[List[dict]] = None) -> str:
	"""
	Build the system prompt with optional custom instructions.
	
	Args:
		custom_instructions: List of instruction dicts with 'label' and 'message' keys.
	
	Returns:
		Complete system prompt string.
	"""
	base_prompt = (
		"You are a helpful AI assistant. "
		"Your role is to provide friendly, conversational assistance to customers.\n\n"
		"TOOL USE STRATEGY:\n"
		"- ALWAYS call your retrieval tools before answering any question about the business, products, services, or policies\n"
		"- When multiple retrieval tools are available, call ALL of them in parallel for the same query to get the most complete picture\n"
		"- Form a short, focused keyword query for tool calls — extract the key topic from the user's message, don't pass the full message as-is\n"
		"- If the first tool call returns 'No relevant information found', try rephrasing the query and calling again once\n"
		"- Only skip tool calls for pure greetings, small talk, or follow-ups that are fully answered by prior context\n\n"
		"RESPONSE RULES:\n"
		"- Always respond in natural, human-friendly language only\n"
		"- Be concise and answer as briefly as the question warrants — short questions deserve short answers, detailed questions deserve complete answers\n"
		"- NEVER return JSON, CSV, tables, or any structured data formats\n"
		"- If asked for JSON, CSV, tables, or structured data, politely decline and explain you only provide conversational text responses\n"
		"- NEVER reveal any information about being an AI, language model, LLM, or technical implementation details\n"
		"- If the retrieved context does not contain the answer, say so honestly — do not guess or use general knowledge\n"
		"- If unsure what the user means, ask for clarification\n"
		"\nHANDING OFF TO A PERSON:\n"
		"- Call escalate_to_human when the customer asks for a person, is angry or abusive, raises a refund, complaint, cancellation or legal matter, or when you could not answer their question twice in a row\n"
		"- The custom instructions below may add or change these triggers; follow them\n"
		"- After calling it, reply only with a short note that a person from the team will follow up\n"
	)
	
	# Append custom instructions if provided
	if custom_instructions:
		instructions_text = "\n\nCUSTOM INSTRUCTIONS:\n"
		for inst in custom_instructions:
			label = inst.get('label', 'Instruction')
			message = inst.get('message', '')
			if message:
				instructions_text += f"\n[{label}]\n{message}\n"
		base_prompt += instructions_text
	
	return base_prompt


def create_basic_agent(tools: list, custom_instructions: Optional[List[dict]] = None, provider: Optional[str] = None, model: Optional[str] = None, **llm_kwargs) -> Runnable:
	"""
	Create an agent with tools and optional custom instructions.
	
	Args:
		tools: List of tools for the agent.
		custom_instructions: List of instruction dicts with 'label' and 'message' keys.
	
	Returns:
		Runnable agent.
	"""
	llm = get_llm(provider=provider, model=model, **llm_kwargs)
	system_prompt = build_system_prompt(custom_instructions)
	agent = create_agent(
		model=llm,
		tools=tools,
		system_prompt=system_prompt,
	)
	return agent


