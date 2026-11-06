"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { AlertTriangle, Check, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { orderApi, type Requirement } from "@/lib/orders-api";
import { formatError } from "@/lib/errors";

const SETUP_HREF: Record<string, string> = {
	channels: "/channels",
	knowledge: "/knowledge",
	products: "/knowledge",
	settings: "/project-settings/orders",
};

type Props = {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	projectId: string;
	onContinue: () => void;
	onTryIt: () => void;
};

export default function RequirementsDialog({ open, onOpenChange, projectId, onContinue, onTryIt }: Props) {
	const [items, setItems] = useState<Requirement[]>([]);
	const [error, setError] = useState<string | null>(null);

	useEffect(() => {
		if (!open) return;
		orderApi.requirements(projectId).then((res) => setItems(res.data)).catch((e) => setError(formatError(e)));
	}, [open, projectId]);

	const blocked = items.some((item) => item.status === "fail");
	const warned = items.some((item) => item.status === "warn");

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent>
				<DialogHeader>
					<DialogTitle>Before you go live</DialogTitle>
					<DialogDescription>These must be in place before the agent can take orders.</DialogDescription>
				</DialogHeader>
				{error && (
					<Alert variant="destructive">
						<AlertDescription>{error}</AlertDescription>
					</Alert>
				)}
				<ul className="space-y-3">
					{items.map((item) => (
						<li key={item.key} className="flex items-start gap-3 text-sm">
							{item.status === "ok" && <Check className="mt-0.5 h-4 w-4 text-green-600" />}
							{item.status === "fail" && <X className="mt-0.5 h-4 w-4 text-destructive" />}
							{item.status === "warn" && <AlertTriangle className="mt-0.5 h-4 w-4 text-amber-600" />}
							<div className="flex-1">
								{item.label}
								{item.status === "fail" && (
									<Link href={SETUP_HREF[item.setup] ?? "/dashboard"} className="ml-2 text-primary underline">
										Set up
									</Link>
								)}
							</div>
						</li>
					))}
				</ul>
				{warned && (
					<Alert>
						<AlertDescription>Some optional parts are missing. You can still go live, but those parts will be skipped.</AlertDescription>
					</Alert>
				)}
				<DialogFooter>
					<Button variant="outline" onClick={onTryIt}>
						Try it
					</Button>
					<Button onClick={onContinue} disabled={blocked || items.length === 0}>
						Continue
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}
