"use client";

import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { ApiError } from "@/lib/api";
import { actionApi, type ActionProgress, type OrderAction } from "@/lib/orders-api";
import { formatError } from "@/lib/errors";

type Props = { projectId: string; orderIds: string[]; disabled: boolean; onRan: () => void };

export default function RunActionMenu({ projectId, orderIds, disabled, onRan }: Props) {
	const [actions, setActions] = useState<OrderAction[]>([]);
	const [pending, setPending] = useState<{ action: OrderAction; numbers: string[] } | null>(null);
	const [tracking, setTracking] = useState<{ action: OrderAction; ids: string[] } | null>(null);
	const [progress, setProgress] = useState<ActionProgress | null>(null);
	const [error, setError] = useState<string | null>(null);
	const onRanRef = useRef(onRan);
	onRanRef.current = onRan;

	useEffect(() => {
		actionApi
			.list(projectId)
			.then((r) => setActions(r.data.filter((a) => a.is_active)))
			.catch((e) => setError(formatError(e)));
	}, [projectId]);

	// Poll the run counts until nothing is queued or running for the selected orders.
	useEffect(() => {
		if (!tracking) return;
		let stopped = false;
		const tick = async () => {
			try {
				const r = await actionApi.progress(tracking.action.id, projectId, tracking.ids);
				if (stopped) return;
				setProgress(r.data);
				if (r.data.counts.queued + r.data.counts.running === 0) {
					setTracking(null);
					onRanRef.current();
				}
			} catch (e) {
				if (!stopped) {
					setError(formatError(e));
					setTracking(null);
				}
			}
		};
		tick();
		const timer = setInterval(tick, 2000);
		return () => {
			stopped = true;
			clearInterval(timer);
		};
	}, [tracking, projectId]);

	async function run(action: OrderAction, force = false) {
		setError(null);
		setPending(null);
		try {
			const r = await actionApi.run(action.id, projectId, orderIds, force);
			if (r.data.queued.length > 0) {
				setProgress(null);
				setTracking({ action, ids: orderIds });
			}
		} catch (e) {
			if (e instanceof ApiError && e.code === "already_sent") {
				setPending({ action, numbers: Array.isArray(e.details) ? e.details : [] });
			} else {
				setError(formatError(e));
			}
		}
	}

	const done = progress ? progress.counts.succeeded + progress.counts.failed : 0;

	return (
		<div className="flex flex-wrap items-center gap-2">
			<DropdownMenu>
				<DropdownMenuTrigger asChild>
					<Button size="sm" variant="outline" disabled={disabled || tracking !== null}>
						Run action ▾
					</Button>
				</DropdownMenuTrigger>
				<DropdownMenuContent>
					{actions.length === 0 && <DropdownMenuItem disabled>No active actions</DropdownMenuItem>}
					{actions.map((a) => (
						<DropdownMenuItem key={a.id} onSelect={() => run(a)}>
							{a.name}
						</DropdownMenuItem>
					))}
				</DropdownMenuContent>
			</DropdownMenu>
			{progress && (
				<span className="text-muted-foreground">
					{tracking
						? `${done} of ${progress.total} done`
						: `Finished: ${progress.counts.succeeded} sent, ${progress.counts.failed} failed`}
				</span>
			)}
			{pending && (
				<div className="w-full rounded-md border border-amber-300 bg-amber-50 p-2 text-sm">
					<p>
						Already sent by {pending.action.name}: {pending.numbers.map((n) => `#${n}`).join(", ") || "some orders"}. Send
						them again?
					</p>
					<div className="mt-2 flex gap-2">
						<Button size="sm" onClick={() => run(pending.action, true)}>
							Send again
						</Button>
						<Button size="sm" variant="outline" onClick={() => setPending(null)}>
							Cancel
						</Button>
					</div>
				</div>
			)}
			{error && <p className="w-full text-destructive">{error}</p>}
		</div>
	);
}
