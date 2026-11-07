"use client";

import { useState } from "react";
import Link from "next/link";
import type { Route } from "next";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import SetupProgress from "@/components/orders/setup/SetupProgress";
import SetupStepSettings from "@/components/orders/setup/SetupStepSettings";
import { useSetupState } from "@/components/orders/setup/useSetupState";

type Props = { projectId: string; kbId: string; onSaved: () => void };

function summary(found: Record<string, number> | undefined): string {
	const zones = found?.zones ?? 0;
	const payments = found?.payment_methods ?? 0;
	return `Found ${zones} delivery zones, ${payments ? `${payments} payment methods` : "no payment methods"}.`;
}

// Step 1. The setup job runs first; its form appears only once the job is ready, unless the user fills it in by hand.
export default function SetupStepSetup({ projectId, kbId, onSaved }: Props) {
	const { state, error, run } = useSetupState(projectId, kbId);
	const [manual, setManual] = useState(false);

	if (!state) {
		return error ? (
			<Alert variant="destructive">
				<AlertDescription>{error}</AlertDescription>
			</Alert>
		) : (
			<Loader2 className="h-5 w-5 animate-spin" />
		);
	}

	const ready = state.status === "ready";
	const showForm = ready || manual;

	function retry(mode: "resume" | "restart") {
		setManual(false);
		void run(mode);
	}

	return (
		<div className="space-y-6">
			{error && (
				<Alert variant="destructive">
					<AlertDescription>{error}</AlertDescription>
				</Alert>
			)}

			{!showForm && (
				<div className="space-y-4">
					<p className="text-sm text-muted-foreground">
						We read your website and documents to fill in what we can. Nothing changes until you save.
					</p>
					<SetupProgress steps={state.steps} />
					{state.status === "running" && (
						<p className="text-sm text-muted-foreground">This can take a few minutes. You can leave this page and come back.</p>
					)}
					{state.status === "needs_input" && (
						<p className="text-sm">
							<Link href={"/knowledge/websites" as Route} className="text-primary underline">
								Add a website or document
							</Link>
							, then check again.
						</p>
					)}
					{state.status === "failed" && (
						<div className="flex flex-wrap items-center gap-3">
							<Button onClick={() => retry("resume")}>Try again</Button>
							<Button variant="ghost" onClick={() => retry("restart")}>
								Start over
							</Button>
							<Button variant="outline" onClick={() => setManual(true)}>
								Fill in myself
							</Button>
						</div>
					)}
					{state.status === "needs_input" && (
						<div className="flex flex-wrap items-center gap-3">
							<Button onClick={() => retry("resume")}>Check again</Button>
							<Button variant="outline" onClick={() => setManual(true)}>
								Fill in myself
							</Button>
						</div>
					)}
				</div>
			)}

			{showForm && (
				<div className="space-y-6">
					{ready && (
						<div className="flex flex-wrap items-center justify-between gap-3">
							<p className="text-sm">{summary(state.report.found)}</p>
							<Button variant="link" className="h-auto p-0" onClick={() => retry("restart")}>
								Run again
							</Button>
						</div>
					)}
					<SetupStepSettings projectId={projectId} onSaved={onSaved} />
				</div>
			)}
		</div>
	);
}
