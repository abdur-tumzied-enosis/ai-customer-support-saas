"use client";

import { Loader2 } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import SetupStepSettings from "@/components/orders/setup/SetupStepSettings";
import PromptPreview from "@/components/orders/settings/PromptPreview";
import FormFieldsEditor from "@/components/orders/settings/FormFieldsEditor";
import OrderInstructions from "@/components/orders/settings/OrderInstructions";
import NotificationsSection from "@/components/orders/settings/NotificationsSection";
import LiveLookupSection from "@/components/orders/settings/LiveLookupSection";
import { useAuth } from "@/hooks/useAuth";

export default function OrderSettingsPage() {
	const { projects } = useAuth();
	const project = projects.length > 0 ? projects[0] : null;
	if (!project) return <Loader2 className="h-5 w-5 animate-spin" />;
	const projectId = String(project.id);

	return (
		<div className="mx-auto max-w-2xl space-y-6">
			<h1 className="text-2xl font-semibold">Order settings</h1>
			<Card>
				<CardHeader>
					<CardTitle>What we tell your agent</CardTitle>
					<CardDescription>Read-only. Built from these settings and your order instructions.</CardDescription>
				</CardHeader>
				<CardContent>
					<PromptPreview projectId={projectId} />
				</CardContent>
			</Card>
			<Card>
				<CardHeader>
					<CardTitle>Delivery, payment and notifications</CardTitle>
				</CardHeader>
				<CardContent>
					<SetupStepSettings projectId={projectId} saveLabel="Save" onSaved={() => undefined} />
				</CardContent>
			</Card>
			<Card>
				<CardHeader>
					<CardTitle>Customer details</CardTitle>
					<CardDescription>Extra questions the agent asks before it confirms an order.</CardDescription>
				</CardHeader>
				<CardContent>
					<FormFieldsEditor projectId={projectId} />
				</CardContent>
			</Card>
			<Card>
				<CardHeader>
					<CardTitle>Order instructions</CardTitle>
					<CardDescription>Your wording for taking orders. The protected rules always apply on top.</CardDescription>
				</CardHeader>
				<CardContent>
					<OrderInstructions projectId={projectId} />
				</CardContent>
			</Card>
			<Card>
				<CardHeader>
					<CardTitle>Notifications</CardTitle>
					<CardDescription>Order emails go to the addresses above.</CardDescription>
				</CardHeader>
				<CardContent>
					<NotificationsSection projectSlug={project.slug} />
				</CardContent>
			</Card>
			<Card>
				<CardHeader>
					<CardTitle>Live price lookup (advanced)</CardTitle>
					<CardDescription>Ask your own API for prices and availability by product key, instead of the catalog.</CardDescription>
				</CardHeader>
				<CardContent>
					<LiveLookupSection projectId={projectId} />
				</CardContent>
			</Card>
		</div>
	);
}
