"use client";

import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { orderApi, type SetupState } from "@/lib/orders-api";
import { formatError } from "@/lib/errors";

const LABELS: Record<string, string> = {
	zones: "delivery zones",
	payment_methods: "payment methods",
	product_candidates: "product candidates",
};

type Props = { projectId: string; kbId: string; onReady: () => void };

export default function SetupStepSetup({ projectId, kbId, onReady }: Props) {
	const [state, setState] = useState<SetupState | null>(null);
	const [error, setError] = useState<string | null>(null);

	useEffect(() => {
		let cancelled = false;
		let timer: ReturnType<typeof setTimeout> | undefined;

		async function poll() {
			try {
				const res = await orderApi.setupState(projectId);
				if (cancelled) return;
				setState(res.data);
				if (res.data.status === "running") timer = setTimeout(poll, 2000);
			} catch (e) {
				if (!cancelled) setError(formatError(e));
			}
		}

		orderApi
			.runSetup(projectId, kbId)
			.then(() => poll())
			.catch((e) => {
				if (!cancelled) setError(formatError(e));
			});
		return () => {
			cancelled = true;
			if (timer) clearTimeout(timer);
		};
	}, [projectId, kbId]);

	const ready = state?.status === "ready";
	const found = state?.report.found ?? {};
	const notFound = state?.report.not_found ?? [];

	return (
		<div className="space-y-4">
			<h2 className="text-lg font-medium">We set it up for you</h2>
			<p className="text-sm text-muted-foreground">
				We read your website and documents to fill in what we can. You can change everything in the next step.
			</p>
			{error && (
				<Alert variant="destructive">
					<AlertDescription>{error}</AlertDescription>
				</Alert>
			)}
			{!ready && !error && <Loader2 className="h-5 w-5 animate-spin" />}
			{ready && (
				<ul className="space-y-1 text-sm">
					{Object.entries(found).map(([key, count]) => (
						<li key={key}>
							Found {count} {LABELS[key] ?? key}
						</li>
					))}
					{notFound.map((item) => (
						<li key={item} className="text-muted-foreground">
							Not found: {item}
						</li>
					))}
				</ul>
			)}
			<div className="flex justify-end">
				<Button onClick={onReady} disabled={!ready}>
					Next
				</Button>
			</div>
		</div>
	);
}
