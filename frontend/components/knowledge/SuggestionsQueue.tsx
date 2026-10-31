"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Check, Lightbulb, Loader2, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { suggestionApi, type MemorySuggestion } from "@/lib/api";
import { formatError } from "@/lib/errors";

const KIND_LABEL = { knowledge: "Knowledge · Q&A", instruction: "Instruction" } as const;

// Lessons the bot drafted from staff answers and handoffs. Nothing reaches the bot until someone approves it.
type Props = {
	projectId: string;
	onApproved: () => void;
	/** How many are still waiting, after each load or decision */
	onCountChange?: (count: number) => void;
};

export default function SuggestionsQueue({ projectId, onApproved, onCountChange }: Props) {
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

	useEffect(() => {
		if (items) onCountChange?.(items.length);
	}, [items, onCountChange]);

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

	return (
		<div className="space-y-4">
			{error && <p className="text-sm text-destructive">{error}</p>}
			{!items ? (
				<Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
			) : items.length === 0 ? (
				<div className="text-center py-8 text-muted-foreground">
					<Lightbulb className="h-12 w-12 mx-auto mb-2 opacity-50" />
					<p>Nothing waiting for review</p>
					<p className="text-sm">Lessons drafted from conversations show up here for approval</p>
				</div>
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
		</div>
	);
}
