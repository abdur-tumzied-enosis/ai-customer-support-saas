"use client";

import { AlertTriangle, Check, Circle, Loader2, X } from "lucide-react";
import { cn } from "@/lib/utils";
import type { SetupStep } from "@/lib/orders-api";

const WORD: Record<SetupStep["status"], string> = {
	pending: "Waiting",
	running: "Running",
	done: "Done",
	failed: "Failed",
	needs_input: "Waiting",
};

function Icon({ status }: { status: SetupStep["status"] }) {
	const base = "mt-0.5 h-4 w-4 shrink-0";
	if (status === "done") return <Check className={cn(base, "text-green-600")} aria-hidden />;
	if (status === "running") return <Loader2 className={cn(base, "motion-safe:animate-spin")} aria-hidden />;
	if (status === "failed") return <X className={cn(base, "text-destructive")} aria-hidden />;
	if (status === "needs_input") return <AlertTriangle className={cn(base, "text-amber-700 dark:text-amber-400")} aria-hidden />;
	return <Circle className={cn(base, "text-muted-foreground")} aria-hidden />;
}

// One row per task. The status is never colour alone: each row also says its state in words for screen readers.
export default function SetupProgress({ steps }: { steps: SetupStep[] }) {
	return (
		<ul aria-live="polite" className="divide-y rounded-md border">
			{steps.map((step) => {
				const text =
					step.status === "failed" || step.status === "needs_input"
						? step.error
						: step.status === "done"
							? step.detail
							: step.status === "running"
								? step.detail || "Working…"
								: "";
				return (
					<li
						key={step.key}
						role={step.status === "failed" ? "alert" : undefined}
						className="flex items-start gap-3 px-4 py-3 text-sm"
					>
						<Icon status={step.status} />
						<div className="min-w-0 flex-1 space-y-1">
							<p className="font-medium">
								{step.label}
								<span className="sr-only"> ({WORD[step.status]})</span>
							</p>
							{text && <p className="text-muted-foreground">{text}</p>}
						</div>
					</li>
				);
			})}
		</ul>
	);
}
