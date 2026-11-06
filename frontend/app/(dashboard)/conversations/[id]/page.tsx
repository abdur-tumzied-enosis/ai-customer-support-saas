"use client";

import { useParams, useRouter } from "next/navigation";
import { useState, useEffect, useRef } from "react";
import MessageList, { ChatMessage } from "@/components/chat/MessageList";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import MessageComposer from "@/components/chat/MessageComposer";
import MemoryDialog from "@/components/chat/MemoryDialog";
import CreateFromChatDialog from "@/components/orders/CreateFromChatDialog";
import ConversationOrders from "@/components/orders/ConversationOrders";
import { conversationApi, featuresApi, type Conversation, type ConversationMessage } from "@/lib/api";
import { useAuth } from "@/hooks/useAuth";
import { formatError } from "@/lib/errors";
import { ArrowLeft, Loader2, Bot } from "lucide-react";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";

export default function ConversationThreadPage() {
	const params = useParams<{ id: string }>();
	const router = useRouter();
	const conversationId = params.id as string;
	const [messages, setMessages] = useState<ChatMessage[]>([]);
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState<string | null>(null);
	const [sending, setSending] = useState(false);
	const [waitingForReply, setWaitingForReply] = useState(false);
	const [conversation, setConversation] = useState<Conversation | null>(null);
	const { projects } = useAuth();
	const [orderDialogOpen, setOrderDialogOpen] = useState(false);
	const [ordersVersion, setOrdersVersion] = useState(0);
	const projectId = projects.length > 0 ? String(projects[0].id) : "";
	const [imageSearchOn, setImageSearchOn] = useState(false);
	const pollingIntervals = useRef<Map<string, NodeJS.Timeout>>(new Map());
	const messagesEndRef = useRef<HTMLDivElement>(null);
	const messagesContainerRef = useRef<HTMLDivElement>(null);

	useEffect(() => {
		if (conversationId) {
			fetchConversation();
			fetchMessages();
		}

		return () => {
			// Cleanup polling intervals on unmount
			pollingIntervals.current.forEach((interval) => clearInterval(interval));
			pollingIntervals.current.clear();
		};
	}, [conversationId]);

	// Staff console: pick up new customer messages, handoffs and auto-resume while the page is open
	// (customer channels have no per-message polling because their messages are not created by this page)
	useEffect(() => {
		if (!conversation || conversation.channel === "self") return;
		const timer = setInterval(async () => {
			try {
				const res = await conversationApi.getMessages(conversationId, 20);
				const latest = res.data.messages.map(toChatMessage);
				setMessages((prev) => {
					const known = new Set(prev.map((m) => m.id));
					const added = latest.filter((m) => !known.has(m.id));
					return added.length ? [...prev, ...added] : prev;
				});
			} catch {
				// transient; next tick retries
			}
			fetchConversation();
		}, 5000);
		return () => clearInterval(timer);
		// eslint-disable-next-line react-hooks/exhaustive-deps -- restart only when the channel changes, not on every refresh
	}, [conversation?.channel, conversationId]);

	// Auto-scroll to bottom when messages change
	useEffect(() => {
		scrollToBottom();
	}, [messages, waitingForReply]);

	function scrollToBottom() {
		if (messagesEndRef.current) {
			messagesEndRef.current.scrollIntoView({ behavior: "smooth", block: "end" });
		}
	}

	const [hasMoreMessages, setHasMoreMessages] = useState(false);
	const [loadingMore, setLoadingMore] = useState(false);
	const [currentOffset, setCurrentOffset] = useState<number | null>(null);

	useEffect(() => {
		if (!projectId) return;
		featuresApi
			.get(projectId)
			.then((f) => setImageSearchOn(f.image_search))
			.catch(() => setImageSearchOn(false));
	}, [projectId]);

	function toChatMessage(msg: ConversationMessage): ChatMessage {
		return {
			id: msg.id,
			role: msg.direction === "in" ? "user" : "assistant",
			content: msg.text,
			sender: msg.sender,
			hasPhoto: msg.has_photo,
			photoMatches: msg.photo_matches,
		};
	}

	async function fetchConversation() {
		try {
			const res = await conversationApi.get(conversationId);
			setConversation(res.data.conversation);
		} catch (err) {
			setError(formatError(err));
		}
	}

	async function fetchMessages(offset: number | null = null, append: boolean = false) {
		if (!append) {
			setLoading(true);
		} else {
			setLoadingMore(true);
		}
		setError(null);
		try {
			// If offset is null, API will return the last 20 messages
			const response = await conversationApi.getMessages(
				conversationId, 
				20, 
				offset ?? undefined
			);
			const apiMessages = response.data.messages;
			
			// Convert API messages to ChatMessage format
			const chatMessages: ChatMessage[] = apiMessages.map(toChatMessage);
			
			if (append) {
				// Prepend older messages to the beginning
				setMessages((prev) => [...chatMessages, ...prev]);
			} else {
				setMessages(chatMessages);
			}

			// Update pagination state
			setHasMoreMessages(response.data.pagination.has_previous);
			setCurrentOffset(response.data.pagination.offset);

			// Start polling for pending/processing messages
			const hasPendingMessages = apiMessages.some(
				(msg) => msg.status === "pending" || msg.status === "processing"
			);
			setWaitingForReply(hasPendingMessages);
			
			apiMessages.forEach((msg) => {
				if (msg.status === "pending" || msg.status === "processing") {
					startPolling(msg.id);
				}
			});
		} catch (err) {
			setError(formatError(err));
		} finally {
			setLoading(false);
			setLoadingMore(false);
		}
	}

	async function loadMoreMessages() {
		if (loadingMore || !hasMoreMessages || currentOffset === null) return;
		
		// Load older messages (decrease offset)
		const previousOffset = Math.max(0, currentOffset - 20);
		await fetchMessages(previousOffset, true);
	}

	async function startPolling(messageId: string) {
		// Clear existing interval if any
		const existingInterval = pollingIntervals.current.get(messageId);
		if (existingInterval) {
			clearInterval(existingInterval);
		}

		setWaitingForReply(true);

		const poll = async () => {
			try {
				const response = await conversationApi.getMessageStatus(messageId);
				const msg = response.data.message;

				if (msg.status === "completed" && msg.agent_reply) {
					// Stop polling
					const interval = pollingIntervals.current.get(messageId);
					if (interval) {
						clearInterval(interval);
						pollingIntervals.current.delete(messageId);
					}

					// Check if there are any other pending messages
					const hasOtherPending = pollingIntervals.current.size > 1;
					setWaitingForReply(hasOtherPending);

					// Add agent reply to messages
					setMessages((prev) => {
						// Check if message already exists
						if (prev.some((m) => m.id === msg.agent_reply!.id)) {
							return prev;
						}
						return [
							...prev,
							{
								id: msg.agent_reply!.id,
								role: "assistant",
								content: msg.agent_reply!.text,
							},
						];
					});
					fetchConversation(); // the reply may have been a handoff
					refreshPhotoMatches(messageId);
				} else if (msg.status === "completed") {
					// Answered as part of a later message's batch, or bot paused: nothing to add
					const interval = pollingIntervals.current.get(messageId);
					if (interval) {
						clearInterval(interval);
						pollingIntervals.current.delete(messageId);
					}
					setWaitingForReply(pollingIntervals.current.size > 0);
					fetchConversation();
					refreshPhotoMatches(messageId);
				} else if (msg.status === "failed") {
					// Stop polling on failure
					const interval = pollingIntervals.current.get(messageId);
					if (interval) {
						clearInterval(interval);
						pollingIntervals.current.delete(messageId);
					}
					
					// Check if there are any other pending messages
					const hasOtherPending = pollingIntervals.current.size > 0;
					setWaitingForReply(hasOtherPending);
				}
			} catch (err) {
				console.error("Polling error:", err);
			}
		};

		// Poll immediately, then every 1.5 seconds
		poll();
		const interval = setInterval(poll, 1500);
		pollingIntervals.current.set(messageId, interval);
	}

	async function handleSend(content: string) {
		if (!content.trim() || sending) return;

		setSending(true);
		setError(null);
		try {
			const response = await conversationApi.sendMessage(conversationId, content);
			const userMessage: ChatMessage = {
				id: response.data.message.id,
				role: "user",
				content: response.data.message.text,
			};

			setMessages((prev) => [...prev, userMessage]);

			// Start polling for agent reply
			if (response.data.message.status === "pending" || response.data.message.status === "processing") {
				startPolling(response.data.message.id);
			}
		} catch (err) {
			setError(formatError(err));
		} finally {
			setSending(false);
		}
	}

	// The photo-match note is written while the reply is processed, so pick it up once the reply has landed
	async function refreshPhotoMatches(messageId: string) {
		try {
			const res = await conversationApi.getMessages(conversationId, 20);
			const fresh = res.data.messages.find((m) => m.id === messageId);
			if (!fresh?.photo_matches) return;
			setMessages((prev) => prev.map((m) => (m.id === messageId ? { ...m, photoMatches: fresh.photo_matches } : m)));
		} catch {
			// the note is optional: leave the message as it is
		}
	}

	async function handleSendPhoto(file: File, text: string) {
		if (sending) return;

		setSending(true);
		setError(null);
		try {
			const response = await conversationApi.sendPhoto(conversationId, file, text);
			setMessages((prev) => [...prev, toChatMessage(response.data.message)]);
			if (response.data.message.status === "pending" || response.data.message.status === "processing") {
				startPolling(response.data.message.id);
			}
		} catch (err) {
			setError(formatError(err));
		} finally {
			setSending(false);
		}
	}

	if (loading) {
		return (
			<div className="flex flex-col h-[calc(100vh-80px)]">
				<div className="flex items-center gap-2 mb-3">
					<Button variant="ghost" size="icon" onClick={() => router.push("/conversations")}>
						<ArrowLeft className="h-4 w-4" />
					</Button>
					<h1 className="text-lg font-semibold">Loading conversation...</h1>
				</div>
				<Card className="flex-1 min-h-0 p-4 flex items-center justify-center">
					<Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
				</Card>
			</div>
		);
	}

	return (
		<div className="flex flex-col h-[calc(100vh-80px)] bg-background">
			{/* Header */}
			<div className="border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
				<div className="flex items-center justify-between px-4 py-3">
					<div className="flex items-center gap-3">
						<Button 
							variant="ghost" 
							size="icon" 
							onClick={() => router.push("/conversations")}
							className="hover:bg-muted"
						>
							<ArrowLeft className="h-4 w-4" />
						</Button>
						<div className="flex items-center gap-2">
							<Avatar className="h-8 w-8">
								<AvatarFallback className="bg-primary/10 text-primary">
									<Bot className="h-4 w-4" />
								</AvatarFallback>
							</Avatar>
							<div>
								<h1 className="text-base font-semibold">{conversation?.name || "Conversation"}</h1>
								<div className="flex items-center gap-2 text-xs text-muted-foreground">
									<span>
										{conversation?.channel === "facebook" ? "Facebook" : "Test chat"}
										{conversation?.is_paused ? " · bot paused" : ""}
									</span>
									{conversation?.knowledge_base_name && (
										<Badge variant="secondary" className="text-xs">{conversation.knowledge_base_name}</Badge>
									)}
								</div>
							</div>
						</div>
					</div>
					<Button variant="outline" size="sm" onClick={() => setOrderDialogOpen(true)} disabled={projects.length === 0}>
						Create order
					</Button>
					<MemoryDialog conversationId={conversationId} />
					<CreateFromChatDialog
						open={orderDialogOpen}
						onOpenChange={setOrderDialogOpen}
						conversationId={conversationId}
						onCreated={() => setOrdersVersion((v) => v + 1)}
					/>
				</div>
			</div>

			{projects.length > 0 && (
				<ConversationOrders projectId={String(projects[0].id)} conversationId={conversationId} version={ordersVersion} />
			)}

			{/* Error Message */}
			{error && (
				<div className="mx-4 mt-3 p-3 bg-destructive/10 border border-destructive/20 rounded-lg text-sm text-destructive animate-in slide-in-from-top-2">
					{error}
				</div>
			)}

			{/* Messages Container */}
			<div 
				ref={messagesContainerRef} 
				className="flex-1 min-h-0 overflow-y-auto px-4 py-6"
			>
				{messages.length === 0 ? (
					<div className="flex flex-col items-center justify-center h-full text-center">
						<div className="rounded-full bg-muted p-4 mb-4">
							<Bot className="h-8 w-8 text-muted-foreground" />
						</div>
						<h3 className="text-lg font-semibold mb-2">Start a conversation</h3>
						<p className="text-sm text-muted-foreground max-w-sm">
							Ask me anything and I&apos;ll help you find the information you need.
						</p>
					</div>
				) : (
					<>
						{hasMoreMessages && (
							<div className="flex justify-center mb-4">
								<Button
									variant="outline"
									size="sm"
									onClick={loadMoreMessages}
									disabled={loadingMore}
									className="text-xs"
								>
									{loadingMore ? (
										<>
											<Loader2 className="h-3 w-3 mr-2 animate-spin" />
											Loading...
										</>
									) : (
										"Load older messages"
									)}
								</Button>
							</div>
						)}
						<MessageList messages={messages} />
						{waitingForReply && (
							<div className="flex items-start gap-3 mt-4 animate-in fade-in slide-in-from-bottom-2">
								<Avatar className="h-8 w-8 shrink-0">
									<AvatarFallback className="bg-muted-foreground/20 text-foreground">
										<Bot className="h-4 w-4" />
									</AvatarFallback>
								</Avatar>
								<div className="flex flex-col gap-1 max-w-[75%]">
									<div className="bg-muted text-foreground rounded-2xl rounded-bl-md px-4 py-2.5 text-sm shadow-sm border border-border/50 flex items-center gap-2">
										<Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
										<span className="text-muted-foreground">Thinking...</span>
									</div>
								</div>
							</div>
						)}
						<div ref={messagesEndRef} className="h-4" />
					</>
				)}
			</div>

			{/* Input Area */}
			{conversation && (
				<MessageComposer
					conversation={conversation}
					onConversationChange={setConversation}
					onStaffMessage={(m) => setMessages((prev) => [...prev, toChatMessage(m)])}
					onSendAsCustomer={handleSend}
					sendingAsCustomer={sending}
					onSendPhoto={imageSearchOn ? handleSendPhoto : undefined}
				/>
			)}
		</div>
	);
}

