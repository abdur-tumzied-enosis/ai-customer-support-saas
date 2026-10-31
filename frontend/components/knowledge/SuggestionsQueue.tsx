"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Check, Lightbulb, Loader2, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import CollapsibleCard from "./CollapsibleCard";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { suggestionApi, type MemorySuggestion } from "@/lib/api";
import { formatError } from "@/lib/errors";

const KIND_LABEL = { knowledge: "Knowledge · Q&A", instruction: "Instruction" } as const;

// Lessons the bot drafted from staff answers and handoffs. Nothing reaches the bot until someone approves it.
export default function SuggestionsQueue({ projectId, onApproved }: { projectId: string; onApproved: () => void }) {
	const [items, setItems] = useState<MemorySuggestion[] | null>(null);
	const [edits, setEdits] = useState<Record<string, { title: string; body: string }>>({});
	const [busyId, setBusyId] = useState<string | null>(null);
	const [error, setError] = useState<string | null>(null);

	const load = useCallback(async () => {
		try {
			setItems((await suggestionApi.list(projectId)).data.suggestions);
		} catch (err) {
			setError(formatError(err));
		}
	}, [projectId]);

	useEffect(() => {
		load();
	}, [load]);

	async function decide(s: MemorySuggestion, approve: boolean) {
		setBusyId(s.id);
		setError(null);
		try {
			if (approve) {
				await suggestionApi.approve(s.id, edits[s.id] ?? { title: s.title, body: s.body });
				onApproved();
			} else {
				await suggestionApi.reject(s.id);
			}
			setItems((prev) => prev?.filter((x) => x.id !== s.id) ?? null);
		} catch (err) {
			setError(formatError(err));
		} finally {
			setBusyId(null);
		}
	}

	function edit(s: MemorySuggestion, field: "title" | "body", value: string) {
		setEdits((prev) => ({ ...prev, [s.id]: { ...(prev[s.id] ?? { title: s.title, body: s.body }), [field]: value } }));
	}

	if (items && items.length === 0 && !error) return null;

	return (
		<CollapsibleCard
			title={<><Lightbulb className="h-5 w-5" /> Suggestions from conversations {items ? `(${items.length})` : ""}</>}
			description="Drafted from staff replies and handoffs. Edit if needed, then approve to teach the bot. Remove anything about one specific customer."
			contentClassName="space-y-4"
		>
				{error && <p className="text-sm text-destructive">{error}</p>}
				{!items ? (
					<Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
				) : (
					items.map((s) => {
						const value = edits[s.id] ?? { title: s.title, body: s.body };
						return (
							<div key={s.id} className="space-y-2 rounded-md border p-3">
								<div className="flex flex-wrap items-center justify-between gap-2">
									<Badge variant="secondary">{KIND_LABEL[s.kind]}</Badge>
									<span className="text-xs text-muted-foreground">
										{s.rationale}
										{s.source_conversation_id && (
											<>
												{" · "}
												<Link href={`/conversations/${s.source_conversation_id}`} className="underline">
													view conversation
												</Link>
											</>
										)}
									</span>
								</div>
								<Input
									aria-label={s.kind === "knowledge" ? "Question" : "Instruction label"}
									value={value.title}
									onChange={(e) => edit(s, "title", e.target.value)}
								/>
								<Textarea
									aria-label={s.kind === "knowledge" ? "Answer" : "Instruction"}
									rows={3}
									value={value.body}
									onChange={(e) => edit(s, "body", e.target.value)}
								/>
								<div className="flex justify-end gap-2">
									<Button variant="outline" size="sm" disabled={busyId === s.id} onClick={() => decide(s, false)}>
										<X className="mr-1 h-4 w-4" />
										Reject
									</Button>
									<Button size="sm" disabled={busyId === s.id || !value.title.trim() || !value.body.trim()} onClick={() => decide(s, true)}>
										<Check className="mr-1 h-4 w-4" />
										Approve
									</Button>
								</div>
							</div>
						);
					})
				)}
		</CollapsibleCard>
	);
}
