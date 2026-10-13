"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Trash2 } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { useAuth } from "@/contexts/AuthContext";
import { conversationApi, ApiError } from "@/lib/api";
import { formatError } from "@/lib/errors";

type Conversation = {
	id: string;
	name: string;
	knowledge_base_id: string;
	knowledge_base_name: string;
	message_count: number;
	last_message: {
		id: string;
		text: string;
		created_at: string;
	} | null;
	created_at: string;
	updated_at: string;
};

export default function ConversationsPage() {
	const { projects, loading: authLoading } = useAuth();
	const router = useRouter();
	const [conversations, setConversations] = useState<Conversation[]>([]);
	const [loading, setLoading] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const [creating, setCreating] = useState(false);
	const [deleting, setDeleting] = useState<string | null>(null);

	// Get the first project (for now, we'll use the first one)
	const currentProject = projects && projects.length > 0 ? projects[0] : null;

	useEffect(() => {
		if (!authLoading && currentProject) {
			fetchConversations();
		}
	}, [authLoading, currentProject]);

	async function fetchConversations() {
		if (!currentProject) return;

		setLoading(true);
		setError(null);
		try {
			const response = await conversationApi.list(currentProject.slug);
			setConversations(response.data.conversations);
		} catch (err) {
			setError(formatError(err));
		} finally {
			setLoading(false);
		}
	}

	async function handleCreateConversation() {
		if (!currentProject) return;

		setCreating(true);
		setError(null);
		try {
			const response = await conversationApi.create({
				project_slug: currentProject.slug,
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
				<Button onClick={handleCreateConversation} disabled={creating}>
					{creating ? "Creating..." : "New Conversation"}
				</Button>
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
							No conversations yet. Create one to get started!
						</div>
					</CardContent>
				) : (
					<ul>
						{conversations.map((conv, idx) => (
							<li key={conv.id}>
								<CardContent className="flex items-center justify-between p-4">
									<div className="flex-1">
										<div className="font-medium">{conv.name || "Unnamed Conversation"}</div>
										<div className="text-xs text-muted-foreground">
											{conv.message_count} messages • {conv.knowledge_base_name}
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
		</div>
	);
}

