"use client";

import { useState } from "react";
import { Brain, Loader2, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { conversationApi, type ConversationMemory } from "@/lib/api";
import { formatError } from "@/lib/errors";

// What the bot remembers about this customer; staff can remove wrong or sensitive facts.
export default function MemoryDialog({ conversationId }: { conversationId: string }) {
	const [memory, setMemory] = useState<ConversationMemory | null>(null);
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState<string | null>(null);

	async function run(call: () => Promise<{ data: ConversationMemory }>) {
		setBusy(true);
		setError(null);
		try {
			setMemory((await call()).data);
		} catch (err) {
			setError(formatError(err));
		} finally {
			setBusy(false);
		}
	}

	return (
		<Dialog onOpenChange={(open) => open && run(() => conversationApi.getMemory(conversationId))}>
			<DialogTrigger asChild>
				<Button variant="ghost" size="sm" className="gap-2">
					<Brain className="h-4 w-4" />
					Memory
				</Button>
			</DialogTrigger>
			<DialogContent className="max-w-lg">
				<DialogHeader>
					<DialogTitle>What the bot remembers</DialogTitle>
					<DialogDescription>
						Facts and summaries picked up from this customer&apos;s messages and used in future replies. Remove anything wrong.
					</DialogDescription>
				</DialogHeader>

				{error && <p className="text-sm text-destructive">{error}</p>}
				{!memory ? (
					<div className="flex justify-center py-6">
						<Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
					</div>
				) : memory.facts.length === 0 && memory.episodes.length === 0 ? (
					<p className="py-4 text-sm text-muted-foreground">Nothing remembered yet.</p>
				) : memory.facts.length === 0 ? null : (
					<ul className="divide-y rounded-md border">
						{memory.facts.map((f) => (
							<li key={f.key} className="flex items-start justify-between gap-3 px-3 py-2">
								<div className="min-w-0">
									<p className="text-xs text-muted-foreground">{f.key.replace(/_/g, " ")}</p>
									<p className="text-sm break-words">{f.value}</p>
								</div>
								<Button
									variant="ghost"
									size="icon"
									aria-label={`Forget ${f.key}`}
									disabled={busy}
									onClick={() => run(() => conversationApi.forgetMemory(conversationId, f.key))}
								>
									<Trash2 className="h-4 w-4" />
								</Button>
							</li>
						))}
					</ul>
				)}

				{memory && memory.episodes.length > 0 && (
					<div className="space-y-2">
						<p className="text-xs font-medium text-muted-foreground">Earlier conversations</p>
						<ul className="space-y-2 text-sm">
							{memory.episodes.map((e) => (
								<li key={e.id} className="rounded-md bg-muted px-3 py-2">
									<span className="text-xs text-muted-foreground">{new Date(e.ended_at).toLocaleDateString()} · </span>
									{e.summary}
								</li>
							))}
						</ul>
					</div>
				)}

				{memory && (memory.facts.length > 0 || memory.episodes.length > 0) && (
					<div className="flex justify-end">
						<Button
							variant="outline"
							size="sm"
							disabled={busy}
							onClick={() => run(() => conversationApi.forgetMemory(conversationId))}
						>
							Forget everything
						</Button>
					</div>
				)}
			</DialogContent>
		</Dialog>
	);
}
