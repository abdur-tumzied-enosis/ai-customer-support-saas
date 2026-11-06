"use client";

import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";
import SetupWizard from "@/components/orders/setup/SetupWizard";
import { useAuth } from "@/hooks/useAuth";

export default function OrderSetupPage() {
	const router = useRouter();
	const { projects } = useAuth();
	const project = projects.length > 0 ? projects[0] : null;
	if (!project) return <Loader2 className="h-5 w-5 animate-spin" />;
	return <SetupWizard projectId={String(project.id)} onDone={() => router.push("/orders")} />;
}
