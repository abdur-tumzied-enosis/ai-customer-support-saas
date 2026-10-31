"use client";

import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";
import { Bot, ImagePlus, Loader2, Send, UserRound } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { conversationApi, type Conversation, type ConversationMessage } from "@/lib/api";
import { formatError } from "@/lib/errors";
import { cn, hoursUntil } from "@/lib/utils";

type Mode = "ai" | "staff";

const MODES: Record<Mode, { bg: string; placeholder: string; hint: string }> = {
	ai: {
		bg: "bg-sky-500/[0.06]",
		placeholder: "Message as the customer…",
		hint: "Sent as a test customer message — the AI replies.",
	},
	staff: {
		bg: "bg-amber-500/[0.08]",
		placeholder: "Reply as staff…",
		hint: "Sent to the customer as staff — pauses the bot.",
	},
};

export default function MessageComposer({
	conversation,
	onConversationChange,
	onStaffMessage,
	onSendAsCustomer,
	sendingAsCustomer,
	onSendPhoto,
}: {
	conversation: Conversation;
	onConversationChange: (c: Conversation) => void;
	onStaffMessage: (m: ConversationMessage) => void;
	onSendAsCustomer: (text: string) => void;
	sendingAsCustomer: boolean;
	onSendPhoto?: (file: File, text: string) => void; // set only when image search is on
}) {
	// Only test chats can simulate the customer; real channels get their customer messages from the platform
	const canSimulate = conversation.channel === "self";
	const [mode, setMode] = useState<Mode>(canSimulate && !conversation.is_paused ? "ai" : "staff");
	const [text, setText] = useState("");
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const textareaRef = useRef<HTMLTextAreaElement>(null);
	const photoInputRef = useRef<HTMLInputElement>(null);
	const sending = busy || (mode === "ai" && sendingAsCustomer);
	const current = MODES[mode];

	useEffect(() => {
		if (conversation.is_paused) setMode("staff");
	}, [conversation.is_paused]);

	useEffect(() => {
		const el = textareaRef.current;
		if (!el) return;
		el.style.height = "auto";
		el.style.height = `${Math.min(el.scrollHeight, 120)}px`;
	}, [text]);

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

	async function onSubmit(e?: FormEvent) {
		e?.preventDefault();
		const value = text.trim();
		if (!value || sending) return;
		if (mode === "ai") {
			onSendAsCustomer(value);
			setText("");
			return;
		}
		const res = await run(() => conversationApi.staffReply(conversation.id, value));
		if (res) {
			setText("");
			onStaffMessage(res.data.message);
			const fresh = await conversationApi.get(conversation.id).catch(() => null);
			if (fresh) onConversationChange(fresh.data.conversation);
		}
	}

	function onPhotoPicked(file: File | undefined) {
		if (photoInputRef.current) photoInputRef.current.value = "";
		if (!file || sending || !onSendPhoto) return;
		onSendPhoto(file, text.trim());
		setText("");
	}

	function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
		if (e.key === "Enter" && !e.shiftKey) {
			e.preventDefault();
			onSubmit();
		}
	}

	return (
		<form onSubmit={onSubmit} className={cn("border-t px-4 py-3 space-y-3 transition-colors duration-200", current.bg)}>
			<div className="flex flex-wrap items-center justify-between gap-2">
				{canSimulate ? (
					<Tabs value={mode} onValueChange={(v) => setMode(v as Mode)}>
						<TabsList aria-label="Sending mode">
							<TabsTrigger value="ai" className="gap-1.5">
								<Bot className="h-3.5 w-3.5" /> Test as customer
							</TabsTrigger>
							<TabsTrigger value="staff" className="gap-1.5">
								<UserRound className="h-3.5 w-3.5" /> Reply as staff
							</TabsTrigger>
						</TabsList>
					</Tabs>
				) : (
					<span className="inline-flex items-center gap-1.5 text-sm font-medium">
						<UserRound className="h-3.5 w-3.5" /> Reply as staff
					</span>
				)}
				<Button type="button" size="sm" variant="outline" onClick={toggleBot} disabled={busy}>
					{conversation.is_paused ? "Resume bot" : "Hand off to staff"}
				</Button>
			</div>

			{conversation.is_paused && (
				<p className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm">
					<strong>Bot paused</strong>
					{conversation.handoff_reason && <> — {conversation.handoff_reason}</>}.{" "}
					{conversation.channel === "facebook" && "Reply here or in the Meta inbox. "}
					{conversation.resumes_at && <>Auto-resumes in {hoursUntil(conversation.resumes_at)}h.</>}
				</p>
			)}

			<div className="flex items-end gap-3">
				<Textarea
					ref={textareaRef}
					aria-label={current.placeholder}
					placeholder={current.placeholder}
					rows={1}
					value={text}
					maxLength={2000}
					onChange={(e) => setText(e.target.value)}
					onKeyDown={onKeyDown}
					className="min-h-[44px] max-h-[120px] resize-none rounded-2xl bg-background px-4 py-3"
				/>
				{canSimulate && mode === "ai" && onSendPhoto && (
					<>
						<input
							ref={photoInputRef}
							type="file"
							accept="image/jpeg,image/png,image/webp"
							className="hidden"
							onChange={(e) => onPhotoPicked(e.target.files?.[0])}
						/>
						<Button
							type="button"
							size="icon"
							variant="outline"
							disabled={sending}
							onClick={() => photoInputRef.current?.click()}
							className="h-11 w-11 shrink-0 rounded-full"
						>
							<ImagePlus className="h-4 w-4" />
							<span className="sr-only">Send a photo as customer</span>
						</Button>
					</>
				)}
				<Button
					type="submit"
					size="icon"
					disabled={sending || !text.trim()}
					className="h-11 w-11 shrink-0 rounded-full shadow-md"
				>
					{sending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
					<span className="sr-only">{mode === "ai" ? "Send as customer" : "Send as staff"}</span>
				</Button>
			</div>

			<p className="text-xs text-muted-foreground">
				{current.hint}
				{mode === "ai" && conversation.is_paused && " The bot is paused, so it won't reply until resumed."}{" "}
				Enter to send, Shift+Enter for a new line.
			</p>
			{error && <p className="text-sm text-destructive">{error}</p>}
		</form>
	);
}
