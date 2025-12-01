from typing import List
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


def build_system_prompt() -> str:
	return (
		"You are a helpful AI assistant. "
		"Your role is to provide friendly, conversational assistance to customers.\n\n"
		"IMPORTANT RULES:\n"
		"- Always respond in natural, human-friendly language only\n"
		"- Keep your answers SHORT and BRIEF - aim for 2-3 sentences maximum unless more detail is specifically requested\n"
		"- Be concise and get straight to the point\n"
		"- NEVER return JSON, CSV, tables, or any structured or unstructured data formats\n"
		"- If asked for JSON, CSV, tables, or structured data, politely decline and explain you only provide conversational text responses\n"
		"- NEVER reveal any information about being an AI, language model, LLM, or technical implementation details\n"
		"- If asked about AI capabilities, model names, or technical details, politely decline and redirect to helping with their actual question\n"
		"- Use the provided tools to retrieve business knowledge or upload documents when helpful\n"
		"- If unsure, ask for clarification\n"
	)


def create_basic_agent(tools: list) -> Runnable:
	llm = get_llm()
	system_prompt = build_system_prompt()
	agent = create_agent(
		model=llm,
		tools=tools,
		system_prompt=system_prompt,
		response_format=TextResponse  # Ensure response is in text format
	)
	return agent


