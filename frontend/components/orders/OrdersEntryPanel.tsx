"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import type { Route } from "next";
import { Check, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useToast } from "@/components/ui/toast";
import { orderApi, type Requirement, type SetupState } from "@/lib/orders-api";
import { formatError } from "@/lib/errors";

// The two checks that block ordering outright. Delivery and product codes are handled inside the setup flow.
const BLOCKERS: Record<string, { href: Route; label: string }> = {
	channel: { href: "/channels" as Route, label: "Open Channels" },
	product_source: { href: "/knowledge/documents" as Route, label: "Open Knowledge" },
};

// One line per setup state; the setup flow itself shows the detail.
function setupLine(state: SetupState | null): string | null {
	if (!state) return null;
	if (state.status === "running") return "We're reading your website and documents.";
	if (state.status === "failed") return "Setup stopped. Open it to see why.";
	if (state.status === "needs_input") return "Setup is waiting for something from you. Open it to see what to add.";
	return null;
}

type Props = { projectId: string; confirmed: boolean; onTryIt: () => void };

export default function OrdersEntryPanel({ projectId, confirmed, onTryIt }: Props) {
	const [requirements, setRequirements] = useState<Requirement[] | null>(null);
	const [setup, setSetup] = useState<SetupState | null>(null);
	const toast = useToast();

	useEffect(() => {
		orderApi.requirements(projectId).then((res) => setRequirements(res.data)).catch((e) => toast.error(formatError(e)));
		orderApi.setupState(projectId).then((res) => setSetup(res.data)).catch(() => setSetup(null));
	}, [projectId]);

	const blockers = (requirements ?? []).filter((item) => item.hard && BLOCKERS[item.key]);
	const line = setupLine(setup);

	return (
		<Card>
			<CardHeader>
				<CardTitle>Start taking orders</CardTitle>
				<CardDescription>The agent takes orders from your chats and confirms each one with you.</CardDescription>
			</CardHeader>
			<CardContent className="space-y-4">
				{line && <p className="text-sm">{line}</p>}
				{blockers.length > 0 && (
					<ul className="space-y-2 text-sm">
						{blockers.map((item) => {
							const link = BLOCKERS[item.key];
							const ok = item.status === "ok";
							return (
								<li key={item.key} className="flex items-center gap-3">
									{ok ? <Check className="h-4 w-4 text-green-600" aria-hidden /> : <X className="h-4 w-4 text-destructive" aria-hidden />}
									<span className="flex-1">
										{item.label}
										<span className="sr-only">{ok ? " (done)" : " (not done)"}</span>
									</span>
									{!ok && (
										<Link href={link.href} className="text-primary underline">
											{link.label}
										</Link>
									)}
								</li>
							);
						})}
					</ul>
				)}
				<div className="flex flex-wrap gap-2">
					<Button asChild>
						<Link href={"/orders/setup" as Route}>{confirmed ? "Continue setup" : "Set up orders"}</Link>
					</Button>
					<Button variant="outline" onClick={onTryIt}>
						Try it
					</Button>
				</div>
			</CardContent>
		</Card>
	);
}
