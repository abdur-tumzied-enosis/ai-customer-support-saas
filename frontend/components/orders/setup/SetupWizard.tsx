"use client";

import { useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Loader2 } from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { knowledgeBaseApi } from "@/lib/api";
import { orderApi } from "@/lib/orders-api";
import { setupStep } from "@/lib/orders-setup";
import { formatError } from "@/lib/errors";
import SetupStepSetup from "@/components/orders/setup/SetupStepSetup";
import SetupStepProducts from "@/components/orders/setup/SetupStepProducts";

type Props = { projectId: string };

export default function SetupWizard({ projectId }: Props) {
	const router = useRouter();
	const requested = useSearchParams().get("step");
	const [kbId, setKbId] = useState<string | null>(null);
	const [confirmed, setConfirmed] = useState<boolean | null>(null);
	const [error, setError] = useState<string | null>(null);

	useEffect(() => {
		orderApi
			.settings(projectId)
			.then((res) => setConfirmed(res.data.settings_confirmed_at !== null))
			.catch((e) => setError(formatError(e)));
		knowledgeBaseApi
			.list(projectId)
			.then((res) => {
				const kbs = res.data.knowledge_bases;
				const kb = kbs.find((k) => k.is_default) ?? kbs[0];
				if (kb) setKbId(kb.id);
				else setError("Add an agent memory before setting up orders.");
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
	if (!kbId || confirmed === null) return <Loader2 className="h-5 w-5 animate-spin" />;

	// Saving the settings is what moves the flow on; the URL cannot skip ahead (see setupStep).
	const step = setupStep(confirmed, requested);
	if (step === 1) {
		return (
			<SetupStepSetup
				projectId={projectId}
				kbId={kbId}
				confirmed={confirmed}
				onSaved={() => {
					// Clears a ?step=1 left by Back; the saved settings now decide that Products is next.
					setConfirmed(true);
					router.replace("/orders/setup");
				}}
			/>
		);
	}
	return <SetupStepProducts projectId={projectId} kbId={kbId} onBack={() => router.push("/orders/setup?step=1")} />;
}
