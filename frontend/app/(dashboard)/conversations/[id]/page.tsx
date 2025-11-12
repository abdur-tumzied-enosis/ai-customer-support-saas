"use client";

import { useParams } from "next/navigation";
import { useState } from "react";
import MessageList, { ChatMessage } from "@/components/chat/MessageList";
import ChatInput from "@/components/chat/ChatInput";
import { Card } from "@/components/ui/card";

export default function ConversationThreadPage() {
	const params = useParams<{ id: string }>();
	const [messages, setMessages] = useState<ChatMessage[]>([
		{ id: "sys-1", role: "assistant", content: "Hi! How can I help today?" },
	]);

	function handleSend(content: string) {
		const id = Math.random().toString(36).slice(2);
		setMessages((prev) => [...prev, { id, role: "user", content }]);
		// Placeholder: simulate assistant reply
		setTimeout(() => {
			const rid = Math.random().toString(36).slice(2);
			setMessages((prev) => [...prev, { id: rid, role: "assistant", content: "Got it. Working on your request…" }]);
		}, 400);
	}

	return (
		<div className="flex flex-col h-[calc(100vh-80px)]">
			<div className="flex items-center justify-between mb-3">
				<h1 className="text-lg font-semibold">Thread: {params.id}</h1>
			</div>
			<Card className="flex-1 min-h-0 p-4 overflow-y-auto">
				<MessageList messages={messages} />
			</Card>
			<div className="mt-3">
				<ChatInput onSend={handleSend} />
			</div>
		</div>
	);
}

