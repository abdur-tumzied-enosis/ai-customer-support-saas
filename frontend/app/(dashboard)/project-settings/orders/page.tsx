"use client";

import { useEffect, useState, type ReactNode } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { ORDERS_CHANGED } from "@/components/orders/OrdersShell";
import { orderApi } from "@/lib/orders-api";
import { formatError } from "@/lib/errors";
import { Loader2 } from "lucide-react";
import SetupStepSettings from "@/components/orders/setup/SetupStepSettings";
import FormFieldsEditor from "@/components/orders/settings/FormFieldsEditor";
import OrderInstructions from "@/components/orders/settings/OrderInstructions";
import NotificationsSection from "@/components/orders/settings/NotificationsSection";
import LiveLookupSection from "@/components/orders/settings/LiveLookupSection";
import ActionsSection from "@/components/orders/actions/ActionsSection";
import { useAuth } from "@/hooks/useAuth";

// GitHub-style section: heading with a rule under it, then the content.
function Section({ title, description, children }: { title: string; description?: string; children: ReactNode }) {
	return (
		<section className="space-y-4 py-8">
			<div className="border-b pb-3">
				<h2 className="text-xl font-semibold">{title}</h2>
				{description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
			</div>
			{children}
		</section>
	);
}

function TurnOffOrders({ projectId }: { projectId: string }) {
	const toast = useToast();
	const [enabled, setEnabled] = useState<boolean | null>(null);
	const [busy, setBusy] = useState(false);

	useEffect(() => {
		orderApi.settings(projectId).then((res) => setEnabled(res.data.enabled)).catch((e) => toast.error(formatError(e)));
	}, [projectId]);

	async function turnOff() {
		setBusy(true);
		try {
			await orderApi.disable(projectId);
			setEnabled(false);
			window.dispatchEvent(new Event(ORDERS_CHANGED));
		} catch (e) {
			toast.error(formatError(e));
		} finally {
			setBusy(false);
		}
	}

	return (
		<div className="space-y-3">
			{enabled === true && (
				<Button variant="destructive" onClick={turnOff} disabled={busy}>
					{busy ? "Turning off…" : "Turn off orders"}
				</Button>
			)}
			{enabled === false && (
				<p className="text-sm text-muted-foreground">
					Orders are off. <Link href="/orders" className="text-primary underline">Go to Orders</Link> to turn them on.
				</p>
			)}
		</div>
	);
}

export default function OrderSettingsPage() {
	const { projects } = useAuth();
	const project = projects.length > 0 ? projects[0] : null;
	if (!project) return <Loader2 className="h-5 w-5 animate-spin" />;
	const projectId = String(project.id);

	return (
		<div className="max-w-3xl">
			<h1 className="pb-2 text-2xl font-semibold">Order settings</h1>
			<Section title="Delivery, payment and notifications">
				<SetupStepSettings projectId={projectId} saveLabel="Save" onSaved={() => undefined} />
			</Section>
			<Section title="Customer details" description="Extra questions the agent asks before it confirms an order.">
				<FormFieldsEditor projectId={projectId} />
			</Section>
			<Section title="Order instructions" description="Your wording for taking orders. The protected rules always apply on top.">
				<OrderInstructions projectId={projectId} />
			</Section>
			<Section title="Notifications" description="Order emails go to the addresses above.">
				<NotificationsSection projectSlug={project.slug} />
			</Section>
			<Section title="Live price lookup (advanced)" description="Ask your own API for prices and availability by product key, instead of the catalog.">
				<LiveLookupSection projectId={projectId} />
			</Section>
			<Section title="Custom actions" description="Send selected orders to a courier or your own API. Admins create and edit actions; everyone can run them.">
				<ActionsSection projectId={projectId} />
			</Section>
			<Section title="Turn orders on or off" description="Turning orders off stops the agent taking orders. Your settings and existing orders are kept, so you can turn it back on without setting up again.">
				<TurnOffOrders projectId={projectId} />
			</Section>
		</div>
	);
}
