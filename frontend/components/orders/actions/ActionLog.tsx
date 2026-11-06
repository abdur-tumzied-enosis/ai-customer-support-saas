"use client";

import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { actionApi, type ActionRun } from "@/lib/orders-api";
import { formatError } from "@/lib/errors";

// Runs are newest first. Secrets are already redacted by the server.
export default function ActionLog({ orderId }: { orderId: string }) {
	const [runs, setRuns] = useState<ActionRun[] | null>(null);
	const [error, setError] = useState<string | null>(null);
	const [busyId, setBusyId] = useState<number | null>(null);

	const load = useCallback(
		() =>
			actionApi
				.runs(orderId)
				.then((r) => setRuns(r.data))
				.catch((e) => setError(formatError(e))),
		[orderId],
	);

	useEffect(() => {
		load();
	}, [load]);

	async function retry(run: ActionRun) {
		setBusyId(run.id);
		setError(null);
		try {
			await actionApi.retry(run.id);
			await load();
		} catch (e) {
			setError(formatError(e));
		} finally {
			setBusyId(null);
		}
	}

	if (runs === null) return <p className="text-muted-foreground">{error ?? "Loading…"}</p>;
	if (runs.length === 0) return <p className="text-muted-foreground">No actions have run for this order.</p>;
	return (
		<div className="space-y-2">
			{error && <p className="text-destructive">{error}</p>}
			{runs.map((run) => (
				<div key={run.id} className="rounded-md border p-2">
					<div className="flex items-center justify-between gap-2">
						<span>
							{run.action_name} · attempt {run.attempt} · <span className="font-medium">{run.status}</span>
							{run.http_status ? ` · HTTP ${run.http_status}` : ""}
						</span>
						{run.status === "failed" && (
							<Button size="sm" variant="outline" disabled={busyId === run.id} onClick={() => retry(run)}>
								Retry
							</Button>
						)}
					</div>
					{run.error && <p className="text-destructive">{run.error}</p>}
					{run.response_excerpt && <pre className="mt-1 whitespace-pre-wrap text-xs text-muted-foreground">{run.response_excerpt}</pre>}
				</div>
			))}
		</div>
	);
}
