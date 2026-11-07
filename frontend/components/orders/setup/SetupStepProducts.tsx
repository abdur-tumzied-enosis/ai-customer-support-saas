"use client";

import { useCallback, useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import CandidateReview from "@/components/knowledge/catalog/CandidateReview";
import { candidateApi, productApi, type CandidatePage } from "@/lib/api";
import { orderApi } from "@/lib/orders-api";
import { formatError } from "@/lib/errors";

type Props = { projectId: string; kbId: string; onBack: () => void; onGoLive: () => void };

export default function SetupStepProducts({ projectId, kbId, onBack, onGoLive }: Props) {
	const [pages, setPages] = useState<CandidatePage[] | null>(null);
	const [counts, setCounts] = useState<Record<string, number>>({});
	const [currency, setCurrency] = useState("BDT");
	const [error, setError] = useState<string | null>(null);
	const [busy, setBusy] = useState(false);

	const load = useCallback(async () => {
		try {
			const [candidates, summary] = await Promise.all([
				candidateApi.list(projectId, kbId),
				productApi.summary(projectId, kbId),
			]);
			setPages(candidates.pages);
			setCounts(summary.counts);
			setCurrency(summary.currency);
		} catch (e) {
			setError(formatError(e));
		}
	}, [projectId, kbId]);

	useEffect(() => {
		load();
	}, [load]);

	async function goLive() {
		setBusy(true);
		setError(null);
		try {
			await orderApi.enable(projectId);
			onGoLive();
		} catch (e) {
			setError(formatError(e));
		} finally {
			setBusy(false);
		}
	}

	if (!pages) {
		return error ? <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert> : <Loader2 className="h-5 w-5 animate-spin" />;
	}

	const active = counts.active ?? 0;
	const needPrice = counts.needs_info ?? 0;

	return (
		<div className="space-y-4">
			<h2 className="text-lg font-medium">Pick products</h2>
			<p className="text-sm text-muted-foreground">Choose the products the agent may sell. Products without a price stay out until you add one.</p>
			{error && (
				<Alert variant="destructive">
					<AlertDescription>{error}</AlertDescription>
				</Alert>
			)}
			<CandidateReview projectId={projectId} kbId={kbId} pages={pages} currency={currency} isAdmin onChanged={load} />
			{needPrice > 0 && (
				<p className="text-sm text-amber-700">
					{needPrice} products still need a price; they won&apos;t be sold until fixed.
				</p>
			)}
			<div className="sticky bottom-0 -mx-1 flex justify-between border-t bg-background/95 px-1 py-3 backdrop-blur">
				<Button variant="outline" onClick={onBack}>
					Back
				</Button>
				<Button onClick={goLive} disabled={busy || active === 0}>
					{busy ? "Going live…" : "Go live"}
				</Button>
			</div>
		</div>
	);
}
