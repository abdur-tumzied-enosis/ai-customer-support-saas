"use client";

import { useEffect, useState } from "react";
import type { Route } from "next";
import Link from "next/link";
import { AlertTriangle, Check, ChevronRight, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { orderApi, type Requirement } from "@/lib/orders-api";
import { formatError } from "@/lib/errors";

// What each check means and how to complete it. Keyed by the check's `key` from the backend (orders/requirements.py).
const HELP = {
	channel: {
		why: "The agent needs a place to talk to customers.",
		steps: [
			"Open Channels and connect Facebook Messenger, Instagram or WhatsApp, or create a website widget.",
			"Make sure the channel is switched on (active).",
		],
		links: [
			{ href: "/channels", label: "Open Channels" },
			{ href: "/widgets", label: "Open website widgets" },
		],
	},
	product_source: {
		why: "The agent answers from your products and prices, so it needs something to read.",
		steps: [
			"Upload a price list or product document, or crawl your website, or connect a product sheet.",
			"Wait until it finishes processing.",
		],
		links: [
			{ href: "/knowledge/documents", label: "Upload documents" },
			{ href: "/knowledge/products", label: "Open Products" },
		],
	},
	currency: {
		why: "Prices, delivery charges and order totals are shown in this currency.",
		steps: ["Open Project settings.", "Choose your currency and click Save."],
		links: [{ href: "/project-settings", label: "Open Project settings" }],
	},
	delivery: {
		why: "Customers pay delivery by area. Without charges, the agent cannot add the right fee to an order.",
		steps: [
			"Open Order settings and find \"Delivery zones and charges\".",
			"Add one row per area you deliver to, with the area name and its fee.",
			"Every row needs both. Type 0 for free delivery.",
			"Click Save.",
		],
		links: [{ href: "/project-settings/orders", label: "Open Order settings" }],
	},
	product_keys: {
		why: "Orders match products by a code, so each product needs a SKU or an external ID.",
		steps: [
			"Open Products.",
			"Open a product and fill in its SKU (or the ID your own system uses).",
			"Repeat for the products you want to sell.",
		],
		links: [{ href: "/knowledge/products", label: "Open Products" }],
	},
} as const;

type HelpKey = keyof typeof HELP;
type HelpLink = { readonly href: Route; readonly label: string };

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
			<DialogContent className="max-h-[90vh] overflow-y-auto">
				<DialogHeader>
					<DialogTitle>Before you go live</DialogTitle>
					<DialogDescription>
						Required items must be done before the agent can take orders. Open an item to see what to do.
					</DialogDescription>
				</DialogHeader>
				{error && (
					<Alert variant="destructive">
						<AlertDescription>{error}</AlertDescription>
					</Alert>
				)}
				<ul className="space-y-3">
					{items.map((item) => {
						const help = HELP[item.key as HelpKey] as
							| { why: string; steps: readonly string[]; links: readonly HelpLink[] }
							| undefined;
						return (
							<li key={item.key} className="text-sm">
								<details open={item.status !== "ok"} className="group">
									<summary className="flex cursor-pointer list-none items-start gap-3">
										{item.status === "ok" && <Check className="mt-0.5 h-4 w-4 text-green-600" />}
										{item.status === "fail" && <X className="mt-0.5 h-4 w-4 text-destructive" />}
										{item.status === "warn" && <AlertTriangle className="mt-0.5 h-4 w-4 text-amber-600" />}
										<span className="flex-1">
											{item.label}
											<span className="ml-2 text-xs text-muted-foreground">
												{item.status === "ok" ? "Done" : item.hard ? "Required" : "Optional, you can still go live"}
											</span>
										</span>
										<ChevronRight className="mt-0.5 h-4 w-4 transition-transform group-open:rotate-90" />
									</summary>
									{help && (
										<div className="ml-7 mt-2 space-y-2 text-muted-foreground">
											<p>{help.why}</p>
											{item.status !== "ok" && (
												<>
													<p className="font-medium text-foreground">How to complete it</p>
													<ol className="list-decimal space-y-1 pl-5">
														{help.steps.map((step) => (
															<li key={step}>{step}</li>
														))}
													</ol>
													<div className="flex flex-wrap gap-3">
														{help.links.map((link) => (
															<Link key={link.href} href={link.href} className="text-primary underline">
																{link.label}
															</Link>
														))}
													</div>
												</>
											)}
										</div>
									)}
								</details>
							</li>
						);
					})}
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
