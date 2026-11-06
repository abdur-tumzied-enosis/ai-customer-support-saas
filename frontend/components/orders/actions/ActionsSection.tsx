"use client";

import { useCallback, useEffect, useState } from "react";
import { Pencil, Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { actionApi, type OrderAction } from "@/lib/orders-api";
import { formatError } from "@/lib/errors";
import ActionEditor from "@/components/orders/actions/ActionEditor";

export default function ActionsSection({ projectId }: { projectId: string }) {
	const [actions, setActions] = useState<OrderAction[] | null>(null);
	const [editing, setEditing] = useState<OrderAction | null>(null);
	const [creating, setCreating] = useState(false);
	const [error, setError] = useState<string | null>(null);

	const load = useCallback(
		() =>
			actionApi
				.list(projectId)
				.then((r) => setActions(r.data))
				.catch((e) => setError(formatError(e))),
		[projectId],
	);

	useEffect(() => {
		load();
	}, [load]);

	async function remove(action: OrderAction) {
		if (!confirm(`Delete the action "${action.name}"? Its run history is deleted with it.`)) return;
		try {
			await actionApi.remove(action.id, projectId);
			load();
		} catch (e) {
			setError(formatError(e));
		}
	}

	return (
		<div className="space-y-3 text-sm">
			{error && <p className="text-destructive">{error}</p>}
			{actions && actions.length === 0 && <p className="text-muted-foreground">No actions yet.</p>}
			{actions?.map((a) => (
				<div key={a.id} className="flex items-center justify-between gap-2 rounded-md border p-2">
					<div>
						<p className="font-medium">{a.name}</p>
						<p className="text-muted-foreground">
							{a.method} {a.url}
							{!a.is_active ? " · inactive" : ""}
							{a.auto_run ? " · runs on new orders" : ""}
						</p>
					</div>
					<div className="flex gap-1">
						<Button variant="ghost" size="icon" aria-label={`Edit ${a.name}`} onClick={() => setEditing(a)}>
							<Pencil className="h-4 w-4" />
						</Button>
						<Button variant="ghost" size="icon" aria-label={`Delete ${a.name}`} onClick={() => remove(a)}>
							<Trash2 className="h-4 w-4" />
						</Button>
					</div>
				</div>
			))}
			<Button variant="outline" size="sm" onClick={() => setCreating(true)}>
				<Plus className="mr-1 h-4 w-4" /> New action
			</Button>
			<ActionEditor
				projectId={projectId}
				action={editing}
				open={creating || editing !== null}
				onOpenChange={(open) => {
					if (!open) {
						setCreating(false);
						setEditing(null);
					}
				}}
				onSaved={load}
			/>
		</div>
	);
}
