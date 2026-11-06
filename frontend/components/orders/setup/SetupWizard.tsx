"use client";

import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { knowledgeBaseApi } from "@/lib/api";
import { formatError } from "@/lib/errors";
import SetupStepSetup from "@/components/orders/setup/SetupStepSetup";
import SetupStepSettings from "@/components/orders/setup/SetupStepSettings";
import SetupStepProducts from "@/components/orders/setup/SetupStepProducts";

const STEPS = ["We set it up", "Settings", "Pick products"];

type Props = { projectId: string; onDone: () => void };

export default function SetupWizard({ projectId, onDone }: Props) {
	const [kbId, setKbId] = useState<string | null>(null);
	const [step, setStep] = useState(1);
	const [error, setError] = useState<string | null>(null);

	useEffect(() => {
		knowledgeBaseApi
			.list(projectId)
			.then((res) => {
				const kbs = res.data.knowledge_bases;
				const kb = kbs.find((k) => k.is_default) ?? kbs[0];
				if (kb) setKbId(kb.id);
				else setError("Add a knowledge base before setting up orders.");
			})
			.catch((e) => setError(formatError(e)));
	}, [projectId]);

	if (error) {
		return (
			<Alert variant="destructive">
				<AlertDescription>{error}</AlertDescription>
			</Alert>
		);
	}
	if (!kbId) return <Loader2 className="h-5 w-5 animate-spin" />;

	return (
		<div className="mx-auto max-w-2xl space-y-6">
			<ol className="flex gap-4 text-sm">
				{STEPS.map((label, i) => (
					<li key={label} className={i + 1 === step ? "font-semibold" : "text-muted-foreground"}>
						{i + 1}. {label}
					</li>
				))}
			</ol>
			{step === 1 && <SetupStepSetup projectId={projectId} kbId={kbId} onReady={() => setStep(2)} />}
			{step === 2 && <SetupStepSettings projectId={projectId} onSaved={() => setStep(3)} />}
			{step === 3 && (
				<SetupStepProducts projectId={projectId} kbId={kbId} onBack={() => setStep(2)} onGoLive={onDone} />
			)}
		</div>
	);
}
