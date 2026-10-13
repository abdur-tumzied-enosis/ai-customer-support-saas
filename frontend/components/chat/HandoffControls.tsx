"use client";

import { useState } from "react";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { conversationApi, type Conversation, type ConversationMessage } from "@/lib/api";
import { formatError } from "@/lib/errors";
import { hoursUntil } from "@/lib/utils";

export default function HandoffControls({
	conversation,
	onConversationChange,
	onStaffMessage,
}: {
	conversation: Conversation;
	onConversationChange: (c: Conversation) => void;
	onStaffMessage: (m: ConversationMessage) => void;
}) {
	const [busy, setBusy] = useState(false);
	const [reply, setReply] = useState("");
	const [error, setError] = useState<string | null>(null);

	async function run<T>(fn: () => Promise<T>) {
		setBusy(true);
		setError(null);
		try {
			return await fn();
		} catch (err) {
			setError(formatError(err));
		} finally {
			setBusy(false);
		}
	}

	async function toggleBot() {
		const res = await run(() =>
			conversation.is_paused ? conversationApi.resume(conversation.id) : conversationApi.handoff(conversation.id)
		);
		if (res) onConversationChange(res.data.conversation);
	}

	async function sendStaffReply() {
		const text = reply.trim();
		if (!text) return;
		const res = await run(() => conversationApi.staffReply(conversation.id, text));
		if (res) {
			setReply("");
			onStaffMessage(res.data.message);
			const fresh = await conversationApi.get(conversation.id).catch(() => null);
			if (fresh) onConversationChange(fresh.data.conversation);
		}
	}

	return (
		<div className="border-t px-4 py-3 space-y-3">
			{conversation.is_paused ? (
				<div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm">
					<span>
						<strong>Bot paused</strong>
						{conversation.handoff_reason && <> — {conversation.handoff_reason}</>}.{" "}
						{conversation.channel === "facebook" && "Reply here or in the Meta inbox. "}
						{conversation.resumes_at && <>Auto-resumes in {hoursUntil(conversation.resumes_at)}h.</>}
					</span>
					<Button size="sm" variant="outline" onClick={toggleBot} disabled={busy}>
						Resume bot
					</Button>
				</div>
			) : (
				<div className="flex justify-end">
					<Button size="sm" variant="outline" onClick={toggleBot} disabled={busy}>
						Hand off to staff
					</Button>
				</div>
			)}

			<div className="flex gap-2 items-end">
				<Textarea
					aria-label="Reply as staff"
					placeholder="Reply as staff…"
					rows={2}
					value={reply}
					maxLength={2000}
					onChange={(e) => setReply(e.target.value)}
				/>
				<Button onClick={sendStaffReply} disabled={busy || !reply.trim()}>
					{busy ? <Loader2 className="h-4 w-4 animate-spin" /> : "Send as staff"}
				</Button>
			</div>
			{error && <p className="text-sm text-destructive">{error}</p>}
		</div>
	);
}
