"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import RequirementsDialog from "@/components/orders/RequirementsDialog";
import { conversationApi } from "@/lib/api";
import { orderApi, type OrderSettings } from "@/lib/orders-api";
import { formatError } from "@/lib/errors";
import { useAuth } from "@/hooks/useAuth";

export default function OrdersPage() {
	const router = useRouter();
	const { projects } = useAuth();
	const project = projects.length > 0 ? projects[0] : null;
	const [settings, setSettings] = useState<OrderSettings | null>(null);
	const [error, setError] = useState<string | null>(null);
	const [dialogOpen, setDialogOpen] = useState(false);
	const [busy, setBusy] = useState(false);

	const load = useCallback(async () => {
		if (!project) return;
		try {
			setSettings((await orderApi.settings(String(project.id))).data);
		} catch (e) {
			setError(formatError(e));
		}
	}, [project]);

	useEffect(() => {
		load();
	}, [load]);

	async function turnOff() {
		if (!project) return;
		setBusy(true);
		try {
			await orderApi.disable(String(project.id));
			await load();
		} catch (e) {
			setError(formatError(e));
		} finally {
			setBusy(false);
		}
	}

	async function tryIt() {
		if (!project) return;
		try {
			await orderApi.setTestMode(String(project.id), true);
			const res = await conversationApi.create({ project_slug: project.slug });
			router.push(`/conversations/${res.data.conversation.id}`);
		} catch (e) {
			setError(formatError(e));
		}
	}

	if (!project || !settings) {
		return error ? (
			<Alert variant="destructive">
				<AlertDescription>{error}</AlertDescription>
			</Alert>
		) : (
			<Loader2 className="h-5 w-5 animate-spin" />
		);
	}

	return (
		<div className="mx-auto max-w-2xl space-y-6">
			<h1 className="text-2xl font-semibold">Orders</h1>
			{error && (
				<Alert variant="destructive">
					<AlertDescription>{error}</AlertDescription>
				</Alert>
			)}
			{settings.enabled ? (
				<Card>
					<CardHeader>
						<CardTitle>Taking orders</CardTitle>
						<CardDescription>The agent takes orders on the channels you chose.</CardDescription>
					</CardHeader>
					<CardContent className="flex gap-2">
						<Button variant="outline" onClick={turnOff} disabled={busy}>
							Turn off
						</Button>
						<Button variant="ghost" onClick={() => router.push("/project-settings/orders")}>
							Order settings
						</Button>
					</CardContent>
				</Card>
			) : (
				<Card>
					<CardHeader>
						<CardTitle>Start taking orders</CardTitle>
						<CardDescription>The agent collects an order in the customer&apos;s language and confirms it with you.</CardDescription>
					</CardHeader>
					<CardContent>
						<Button onClick={() => setDialogOpen(true)}>Start taking orders</Button>
					</CardContent>
				</Card>
			)}
			<RequirementsDialog
				open={dialogOpen}
				onOpenChange={setDialogOpen}
				projectId={String(project.id)}
				onContinue={() => router.push("/orders/setup")}
				onTryIt={tryIt}
			/>
		</div>
	);
}
