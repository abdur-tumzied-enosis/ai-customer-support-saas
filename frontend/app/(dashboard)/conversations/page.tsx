"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Trash2 } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { useAuth } from "@/contexts/AuthContext";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { conversationApi, knowledgeBaseApi, ApiError, type ConversationListItem, type KnowledgeBase } from "@/lib/api";
import { getStoredKbId, kbOptionLabel, pickKb } from "@/lib/knowledgeBase";
import { formatError } from "@/lib/errors";
import { timeAgo } from "@/lib/utils";

export default function ConversationsPage() {
	const { projects, loading: authLoading } = useAuth();
	const router = useRouter();
	const [conversations, setConversations] = useState<ConversationListItem[]>([]);
	const [needsHumanOnly, setNeedsHumanOnly] = useState(false);
	const [needsHumanCount, setNeedsHumanCount] = useState(0);
	const [loading, setLoading] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const [creating, setCreating] = useState(false);
	const [deleting, setDeleting] = useState<string | null>(null);
	const [newChatOpen, setNewChatOpen] = useState(false);
	const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
	const [newChatKbId, setNewChatKbId] = useState("");

	// Get the first project (for now, we'll use the first one)
	const currentProject = projects && projects.length > 0 ? projects[0] : null;

	useEffect(() => {
		if (!authLoading && currentProject) {
			fetchConversations();
		}
	}, [authLoading, currentProject, needsHumanOnly]);

	async function fetchConversations() {
		if (!currentProject) return;

		setLoading(true);
		setError(null);
		try {
			const response = await conversationApi.list(currentProject.slug, { needsHuman: needsHumanOnly });
			setConversations(response.data.conversations);
			setNeedsHumanCount(response.data.needs_human_count);
		} catch (err) {
			setError(formatError(err));
		} finally {
			setLoading(false);
		}
	}

	async function openNewChat() {
		if (!currentProject) return;
		setError(null);
		try {
			const projectId = String(currentProject.id);
			const response = await knowledgeBaseApi.list(projectId);
			setKnowledgeBases(response.data.knowledge_bases);
			setNewChatKbId(pickKb(response.data.knowledge_bases, getStoredKbId(projectId))?.id ?? "");
			setNewChatOpen(true);
		} catch (err) {
			setError(formatError(err));
		}
	}

	async function handleCreateConversation() {
		if (!currentProject || !newChatKbId) return;

		setCreating(true);
		setError(null);
		try {
			const response = await conversationApi.create({
				project_slug: currentProject.slug,
				knowledge_base_id: newChatKbId,
			});
			router.push(`/conversations/${response.data.conversation.id}`);
		} catch (err) {
			setError(formatError(err));
			setCreating(false);
		}
	}

	async function handleDeleteConversation(conversationId: string) {
		if (!confirm("Are you sure you want to delete this conversation? This action cannot be undone.")) {
			return;
		}

		setDeleting(conversationId);
		setError(null);
		try {
			await conversationApi.delete(conversationId);
			setConversations(conversations.filter(c => c.id !== conversationId));
		} catch (err) {
			setError(formatError(err));
		} finally {
			setDeleting(null);
		}
	}

	if (authLoading || loading) {
		return (
			<div className="space-y-4">
				<h1 className="text-2xl font-semibold">Conversations</h1>
				<Card>
					<CardContent className="p-4">
						<div className="text-muted-foreground">Loading conversations...</div>
					</CardContent>
				</Card>
			</div>
		);
	}

	if (!currentProject) {
		return (
			<div className="space-y-4">
				<h1 className="text-2xl font-semibold">Conversations</h1>
				<Card>
					<CardContent className="p-4">
						<div className="text-muted-foreground">No project available</div>
					</CardContent>
				</Card>
			</div>
		);
	}

	return (
		<div className="space-y-4">
			<div className="flex items-center justify-between">
				<h1 className="text-2xl font-semibold">Conversations</h1>
				<div className="flex gap-2">
					<Button
						variant={needsHumanOnly ? "default" : "outline"}
						onClick={() => setNeedsHumanOnly((v) => !v)}
					>
						Needs human ({needsHumanCount})
					</Button>
					<Button onClick={openNewChat} disabled={creating}>
						New test chat
					</Button>
				</div>
			</div>

			{error && (
				<Card className="border-destructive">
					<CardContent className="p-4">
						<div className="text-destructive text-sm">{error}</div>
					</CardContent>
				</Card>
			)}

			<Card>
				{conversations.length === 0 ? (
					<CardContent className="p-4">
						<div className="text-center text-muted-foreground py-8">
							No conversations yet. Start a test chat to try your knowledge base.
						</div>
					</CardContent>
				) : (
					<ul>
						{conversations.map((conv, idx) => (
							<li key={conv.id}>
								<CardContent className="flex items-center justify-between p-4">
									<div className="flex-1">
										<div className="font-medium flex flex-wrap items-center gap-2">
											{conv.name || "Unnamed Conversation"}
											<Badge variant="outline">{conv.channel === "self" ? "Test" : conv.channel === "facebook" ? "Facebook" : conv.channel}</Badge>
											<Badge variant="secondary">{conv.knowledge_base_name}</Badge>
											{conv.is_paused && <Badge variant="destructive">Needs human</Badge>}
										</div>
										{conv.is_paused && conv.handoff_at && (
											<div className="text-xs text-destructive">
												{conv.handoff_reason || "Handed off"} • {timeAgo(conv.handoff_at)}
											</div>
										)}
										<div className="text-xs text-muted-foreground">
											{conv.message_count} messages
											{conv.last_message && (
												<> • {new Date(conv.last_message.created_at).toLocaleDateString()}</>
											)}
										</div>
									</div>
									<div className="flex items-center gap-2">
										<Button variant="ghost" asChild>
											<Link href={`/conversations/${conv.id}`}>
												Open →
											</Link>
										</Button>
										<Button
											variant="ghost"
											size="sm"
											onClick={() => handleDeleteConversation(conv.id)}
											disabled={deleting === conv.id}
											className="text-destructive hover:text-destructive hover:bg-destructive/10"
										>
											<Trash2 className="w-4 h-4" />
										</Button>
									</div>
								</CardContent>
								{idx < conversations.length - 1 && <Separator />}
							</li>
						))}
					</ul>
				)}
			</Card>

			<Dialog open={newChatOpen} onOpenChange={setNewChatOpen}>
				<DialogContent>
					<DialogHeader>
						<DialogTitle>New test chat</DialogTitle>
						<DialogDescription>The bot answers only from the knowledge base you pick.</DialogDescription>
					</DialogHeader>
					<div className="space-y-2">
						<Label htmlFor="new-chat-kb">Knowledge base</Label>
						<Select value={newChatKbId || undefined} onValueChange={setNewChatKbId}>
							<SelectTrigger id="new-chat-kb">
								<SelectValue placeholder="Select a knowledge base" />
							</SelectTrigger>
							<SelectContent>
								{knowledgeBases.map((kb) => (
									<SelectItem key={kb.id} value={kb.id}>
										{kbOptionLabel(kb)}
									</SelectItem>
								))}
							</SelectContent>
						</Select>
					</div>
					<DialogFooter>
						<Button variant="outline" onClick={() => setNewChatOpen(false)}>Cancel</Button>
						<Button onClick={handleCreateConversation} disabled={creating || !newChatKbId}>
							{creating ? "Creating..." : "Start chat"}
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>
		</div>
	);
}

