"use client";

import { useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Loader2 } from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { knowledgeBaseApi } from "@/lib/api";
import { formatError } from "@/lib/errors";
import SetupStepSetup from "@/components/orders/setup/SetupStepSetup";
import SetupStepSettings from "@/components/orders/setup/SetupStepSettings";
import SetupStepProducts from "@/components/orders/setup/SetupStepProducts";

type Props = { projectId: string; onDone: () => void };

export default function SetupWizard({ projectId, onDone }: Props) {
	const [kbId, setKbId] = useState<string | null>(null);
	const router = useRouter();
	const step = Math.min(3, Math.max(1, Number(useSearchParams().get("step")) || 1));
	const setStep = (n: number) => router.push(`/orders/setup?step=${n}`);
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
			{step === 1 && <SetupStepSetup projectId={projectId} kbId={kbId} onReady={() => setStep(2)} />}
			{step === 2 && <SetupStepSettings projectId={projectId} onSaved={() => setStep(3)} />}
			{step === 3 && (
				<SetupStepProducts projectId={projectId} kbId={kbId} onBack={() => setStep(2)} onGoLive={onDone} />
			)}
		</div>
	);
}
