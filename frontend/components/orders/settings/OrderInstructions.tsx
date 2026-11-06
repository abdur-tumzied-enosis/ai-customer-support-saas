"use client";

import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { orderInstructionApi, type OrderInstruction } from "@/lib/orders-api";
import { formatError } from "@/lib/errors";

const MAX_CHARS = 2000;

type Editing = { id: string | null; label: string; message: string };
type Notice = { kind: "ok" | "error"; text: string };

export default function OrderInstructions({ projectId }: { projectId: string }) {
	const [items, setItems] = useState<OrderInstruction[]>([]);
	const [editing, setEditing] = useState<Editing | null>(null);
	const [suggestion, setSuggestion] = useState<{ suggestion: string; why: string } | null>(null);
	const [notice, setNotice] = useState<Notice | null>(null);
	const [busy, setBusy] = useState(false);

	const load = useCallback(async () => {
		try {
			const res = await orderInstructionApi.list(projectId);
			setItems(res.data.instructions.filter((i) => i.scope === "order"));
		} catch (e) {
			setNotice({ kind: "error", text: formatError(e) });
		}
	}, [projectId]);

	useEffect(() => {
		load();
	}, [load]);

	async function save() {
		if (!editing) return;
		setBusy(true);
		setNotice(null);
		try {
			const res = editing.id
				? await orderInstructionApi.update(editing.id, { label: editing.label, message: editing.message })
				: await orderInstructionApi.create(projectId, { label: editing.label, message: editing.message, scope: "order" });
			const warnings = res.data.warnings ?? [];
			setNotice({
				kind: "ok",
				text: warnings.length > 0 ? `Saved. Please check: ${warnings.join("; ")}` : "Saved.",
			});
			setEditing(null);
			setSuggestion(null);
			await load();
		} catch (e) {
			setNotice({ kind: "error", text: formatError(e) });
		} finally {
			setBusy(false);
		}
	}

	async function improve() {
		if (!editing) return;
		setBusy(true);
		setNotice(null);
		try {
			const res = await orderInstructionApi.improve(projectId, editing.message);
			setSuggestion(res.data);
		} catch (e) {
			setNotice({ kind: "error", text: formatError(e) });
		} finally {
			setBusy(false);
		}
	}

	return (
		<div className="space-y-4">
			{notice && (
				<Alert variant={notice.kind === "error" ? "destructive" : "default"}>
					<AlertDescription>{notice.text}</AlertDescription>
				</Alert>
			)}

			{items.map((item) => (
				<div key={item.id} className="flex items-start justify-between gap-2 rounded-md border p-3 text-sm">
					<div className="space-y-1">
						<div className="flex items-center gap-2">
							<span className="font-medium">{item.label}</span>
							<Badge>Orders</Badge>
						</div>
						<p className="text-muted-foreground">{item.message}</p>
					</div>
					<Button variant="ghost" size="sm" onClick={() => setEditing({ id: item.id, label: item.label, message: item.message })}>
						Edit
					</Button>
				</div>
			))}

			{editing ? (
				<div className="space-y-3 rounded-md border p-3">
					<div className="space-y-1">
						<Label htmlFor="order-instruction-label">Label</Label>
						<Input
							id="order-instruction-label"
							value={editing.label}
							onChange={(e) => setEditing({ ...editing, label: e.target.value })}
						/>
					</div>
					<div className="space-y-1">
						<Label htmlFor="order-instruction-message">What the agent should do when taking orders</Label>
						<Textarea
							id="order-instruction-message"
							rows={5}
							value={editing.message}
							onChange={(e) => setEditing({ ...editing, message: e.target.value })}
						/>
						<p className={`text-xs ${editing.message.length > MAX_CHARS ? "text-destructive" : "text-muted-foreground"}`}>
							{editing.message.length}/{MAX_CHARS}
						</p>
					</div>
					{suggestion && (
						<div className="space-y-2 rounded-md bg-muted p-3 text-sm">
							<p className="whitespace-pre-wrap">{suggestion.suggestion}</p>
							<p className="text-muted-foreground">{suggestion.why}</p>
							<div className="flex gap-2">
								<Button size="sm" onClick={() => setEditing({ ...editing, message: suggestion.suggestion })}>
									Use this
								</Button>
								<Button size="sm" variant="ghost" onClick={() => setSuggestion(null)}>
									Dismiss
								</Button>
							</div>
						</div>
					)}
					<div className="flex flex-wrap justify-between gap-2">
						<Button variant="outline" onClick={improve} disabled={busy || !editing.message.trim()}>
							Improve with AI
						</Button>
						<div className="flex gap-2">
							<Button variant="ghost" onClick={() => { setEditing(null); setSuggestion(null); }}>
								Cancel
							</Button>
							<Button
								onClick={save}
								disabled={busy || !editing.label.trim() || !editing.message.trim() || editing.message.length > MAX_CHARS}
							>
								Save
							</Button>
						</div>
					</div>
				</div>
			) : (
				<Button variant="outline" onClick={() => setEditing({ id: null, label: "", message: "" })}>
					Add order instruction
				</Button>
			)}
		</div>
	);
}
