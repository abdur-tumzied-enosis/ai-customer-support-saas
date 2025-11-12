from typing import List
from django.conf import settings
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.agents import create_agent
from langchain_core.runnables import Runnable


def get_llm():
	return ChatGoogleGenerativeAI(model="gemini-2.5-flash", api_key=settings.GOOGLE_API_KEY)


def build_system_prompt() -> str:
	return (
		f"You are the AI assistant"
		"Use the provided tools to retrieve business knowledge or upload documents when helpful. "
		"Prefer concise, helpful answers. If unsure, ask for clarification."
	)


def create_basic_agent(tools: list) -> Runnable:
	llm = get_llm()
	system_prompt = build_system_prompt()
	agent = create_agent(model=llm, tools=tools, system_prompt=system_prompt)
	return agent


