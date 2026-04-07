from typing import List, Optional
from django.conf import settings
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.agents import create_agent
from langchain_core.runnables import Runnable
from pydantic import BaseModel


class TextResponse(BaseModel):
	"""Response format for text-only output."""
	text: str


def get_llm():
	return ChatGoogleGenerativeAI(model="gemini-2.5-flash", api_key=settings.GOOGLE_API_KEY)


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


def create_basic_agent(tools: list, custom_instructions: Optional[List[dict]] = None) -> Runnable:
	"""
	Create an agent with tools and optional custom instructions.
	
	Args:
		tools: List of tools for the agent.
		custom_instructions: List of instruction dicts with 'label' and 'message' keys.
	
	Returns:
		Runnable agent.
	"""
	llm = get_llm()
	system_prompt = build_system_prompt(custom_instructions)
	agent = create_agent(
		model=llm,
		tools=tools,
		system_prompt=system_prompt,
		response_format=TextResponse  # Ensure response is in text format
	)
	return agent


