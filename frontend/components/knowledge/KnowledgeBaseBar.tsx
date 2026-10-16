"use client";

import { useState } from "react";
import { Database, Pencil, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { knowledgeBaseApi, ApiError, type KnowledgeBase } from "@/lib/api";
import { kbOptionLabel } from "@/lib/knowledgeBase";
import { formatError } from "@/lib/errors";

type Props = {
	projectId: string;
	knowledgeBases: KnowledgeBase[];
	selected: KnowledgeBase | undefined;
	onSelect: (kb: KnowledgeBase) => void;
	/** After a create or rename; the parent refetches the list and selects `kb`. */
	onSaved: (kb: KnowledgeBase) => void;
};

export default function KnowledgeBaseBar({ projectId, knowledgeBases, selected, onSelect, onSaved }: Props) {
	const [mode, setMode] = useState<"create" | "rename" | null>(null);
	const [name, setName] = useState("");
	const [saving, setSaving] = useState(false);
	const [error, setError] = useState<string | null>(null);

	function open(next: "create" | "rename") {
		setMode(next);
		setName(next === "rename" ? selected?.name ?? "" : "");
		setError(null);
	}

	async function save() {
		if (!name.trim() || saving) return;
		setSaving(true);
		setError(null);
		try {
			const res = mode === "rename" && selected
				? await knowledgeBaseApi.rename(selected.id, name)
				: await knowledgeBaseApi.create(projectId, name);
			setMode(null);
			onSaved(res.data.knowledge_base);
		} catch (err) {
			setError(err instanceof ApiError && err.code === "duplicate_name"
				? "A knowledge base with this name already exists"
				: formatError(err));
		} finally {
			setSaving(false);
		}
	}

	return (
		<div className="flex flex-wrap items-center gap-2 rounded-lg border bg-card p-3">
			<Database className="h-5 w-5 text-primary" />
			<span className="text-sm font-medium">Knowledge base:</span>
			<Select
				value={selected?.id}
				onValueChange={(id: string) => {
					const kb = knowledgeBases.find((k) => k.id === id);
					if (kb) onSelect(kb);
				}}
			>
				<SelectTrigger className="w-72">
					<SelectValue placeholder="Loading..." />
				</SelectTrigger>
				<SelectContent>
					{knowledgeBases.map((kb) => (
						<SelectItem key={kb.id} value={kb.id}>
							{kbOptionLabel(kb)}
						</SelectItem>
					))}
				</SelectContent>
			</Select>
			<Button variant="ghost" size="icon" onClick={() => open("rename")} disabled={!selected} title="Rename knowledge base">
				<Pencil className="h-4 w-4" />
			</Button>
			<Button variant="outline" onClick={() => open("create")}>
				<Plus className="mr-2 h-4 w-4" />
				New knowledge base
			</Button>

			<Dialog open={mode !== null} onOpenChange={(isOpen) => !isOpen && setMode(null)}>
				<DialogContent>
					<DialogHeader>
						<DialogTitle>{mode === "rename" ? "Rename knowledge base" : "New knowledge base"}</DialogTitle>
					</DialogHeader>
					<div className="space-y-2">
						<Label htmlFor="kb-name">Name</Label>
						<Input
							id="kb-name"
							value={name}
							maxLength={100}
							autoFocus
							onChange={(e) => setName(e.target.value)}
							onKeyDown={(e) => e.key === "Enter" && save()}
						/>
						{error && <p className="text-sm text-destructive">{error}</p>}
					</div>
					<DialogFooter>
						<Button variant="outline" onClick={() => setMode(null)}>Cancel</Button>
						<Button onClick={save} disabled={saving || !name.trim()}>
							{saving ? "Saving..." : mode === "rename" ? "Rename" : "Create"}
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>
		</div>
	);
}
