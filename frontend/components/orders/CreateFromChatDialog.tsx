"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { useToast } from "@/components/ui/toast";
import { orderFromChatApi, type PrefillLine } from "@/lib/orders-api";
import { formatError } from "@/lib/errors";

const CUSTOMER_FIELDS: { key: string; label: string }[] = [
	{ key: "customer_name", label: "Name" },
	{ key: "customer_phone", label: "Phone" },
	{ key: "customer_address", label: "Address" },
	{ key: "zone_name", label: "Delivery zone" },
];

type Props = { open: boolean; onOpenChange: (open: boolean) => void; conversationId: string; onCreated: () => void };

export default function CreateFromChatDialog({ open, onOpenChange, conversationId, onCreated }: Props) {
	const [lines, setLines] = useState<PrefillLine[]>([]);
	const [customer, setCustomer] = useState<Record<string, string>>({});
	const [needsPrice, setNeedsPrice] = useState<{ product_id: string; name: string }[]>([]);
	const [loading, setLoading] = useState(false);
	const [busy, setBusy] = useState(false);
	const toast = useToast();
	const [problems, setProblems] = useState<string[]>([]);

	useEffect(() => {
		if (!open) return;
		setLoading(true);
		setProblems([]);
		orderFromChatApi
			.prefill(conversationId)
			.then((res) => {
				setLines(res.data.items);
				setCustomer(res.data.customer as Record<string, string>);
				setNeedsPrice(res.data.needs_price);
			})
			.catch((e) => toast.error(formatError(e)))
			.finally(() => setLoading(false));
	}, [open, conversationId]);

	async function create() {
		setBusy(true);
		setProblems([]);
		try {
			const res = await orderFromChatApi.create(
				conversationId,
				lines.map(({ product_id, variant_id, quantity }) => ({ product_id, variant_id, quantity })),
				customer
			);
			if (res.data.order_number) {
				onCreated();
				onOpenChange(false);
			} else {
				setProblems((res.data.problems ?? []).map((p) => p.message));
			}
		} catch (e) {
			toast.error(formatError(e));
		} finally {
			setBusy(false);
		}
	}

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-lg">
				<DialogHeader>
					<DialogTitle>Create order from this chat</DialogTitle>
					<DialogDescription>Check the lines and details the assistant read from the chat, then create the order.</DialogDescription>
				</DialogHeader>
				{loading && <p className="text-sm text-muted-foreground">Reading the chat…</p>}
				{problems.length > 0 && (
					<Alert variant="destructive">
						<AlertDescription>
							<ul className="list-disc pl-4">
								{problems.map((p) => (
									<li key={p}>{p}</li>
								))}
							</ul>
						</AlertDescription>
					</Alert>
				)}
				{!loading && (
					<div className="space-y-4 text-sm">
						<section className="space-y-2">
							<h3 className="font-medium">Products</h3>
							{lines.length === 0 && <p className="text-muted-foreground">No products found in the chat.</p>}
							{lines.map((line, i) => (
								<div key={`${line.product_id}-${i}`} className="flex items-center gap-2">
									<span className="flex-1">{line.name}</span>
									<Input
										type="number"
										min={1}
										max={100}
										className="w-20"
										aria-label={`Quantity of ${line.name}`}
										value={line.quantity}
										onChange={(e) =>
											setLines(lines.map((l, j) => (j === i ? { ...l, quantity: Number(e.target.value) } : l)))
										}
									/>
									<Button variant="ghost" size="sm" onClick={() => setLines(lines.filter((_, j) => j !== i))}>
										Remove
									</Button>
								</div>
							))}
							{needsPrice.map((item) => (
								<p key={item.product_id} className="text-amber-700">
									{item.name} has no price, so it cannot be added.{" "}
									<Link href="/knowledge" className="underline">
										Set its price
									</Link>
								</p>
							))}
						</section>

						<section className="grid gap-3 sm:grid-cols-2">
							{CUSTOMER_FIELDS.map((field) => (
								<div key={field.key} className="space-y-1">
									<Label htmlFor={field.key}>{field.label}</Label>
									<Input
										id={field.key}
										value={customer[field.key] ?? ""}
										onChange={(e) => setCustomer({ ...customer, [field.key]: e.target.value })}
									/>
								</div>
							))}
						</section>
					</div>
				)}
				<DialogFooter>
					<Button variant="outline" onClick={() => onOpenChange(false)}>
						Cancel
					</Button>
					<Button onClick={create} disabled={busy || loading || lines.length === 0}>
						{busy ? "Creating…" : "Create order"}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}
