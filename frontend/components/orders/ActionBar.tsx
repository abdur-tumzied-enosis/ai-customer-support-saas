"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import RunActionMenu from "@/components/orders/actions/RunActionMenu";

const STATUSES = ["new", "confirmed", "shipped", "delivered", "cancelled"];

type Props = {
	count: number;
	busy: boolean;
	onApply: (status: string) => void;
	projectId: string;
	ids: string[];
	onRan: () => void;
};

export default function ActionBar({ count, busy, onApply, projectId, ids, onRan }: Props) {
	const [status, setStatus] = useState("confirmed");
	if (count === 0) return null;
	return (
		<div className="flex flex-wrap items-center gap-2 rounded-md border bg-muted p-2 text-sm">
			<span>{count} selected</span>
			<select
				className="h-9 rounded-md border border-input bg-background px-2 text-sm"
				value={status}
				onChange={(e) => setStatus(e.target.value)}
				aria-label="New status"
			>
				{STATUSES.map((s) => (
					<option key={s} value={s}>
						{s}
					</option>
				))}
			</select>
			<Button size="sm" onClick={() => onApply(status)} disabled={busy}>
				Set status
			</Button>
			<RunActionMenu projectId={projectId} orderIds={ids} disabled={busy} onRan={onRan} />
		</div>
	);
}
