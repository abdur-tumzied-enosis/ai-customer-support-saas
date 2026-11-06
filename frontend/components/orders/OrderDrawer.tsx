"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { currencySymbol } from "@/components/knowledge/catalog/ProductTable";
import { orderApi, type OrderDetail } from "@/lib/orders-api";
import { formatError } from "@/lib/errors";

const STATUSES = ["new", "confirmed", "shipped", "delivered", "cancelled"];

type Draft = {
	quantities: Record<string, number>;
	customer_name: string;
	customer_phone: string;
	customer_address: string;
	advance_paid: string;
	tracking_number: string;
	internal_note: string;
	status: string;
};

function toDraft(o: OrderDetail): Draft {
	return {
		quantities: Object.fromEntries(o.items.map((i) => [i.id, i.quantity])),
		customer_name: o.customer_name,
		customer_phone: o.customer_phone,
		customer_address: o.customer_address,
		advance_paid: o.advance_paid,
		tracking_number: o.tracking_number,
		internal_note: o.internal_note,
		status: o.status,
	};
}

type Props = { orderId: string | null; onOpenChange: (open: boolean) => void; onChanged: () => void };

export default function OrderDrawer({ orderId, onOpenChange, onChanged }: Props) {
	const [order, setOrder] = useState<OrderDetail | null>(null);
	const [draft, setDraft] = useState<Draft | null>(null);
	const [error, setError] = useState<string | null>(null);
	const [busy, setBusy] = useState(false);

	useEffect(() => {
		if (!orderId) return;
		setError(null);
		orderApi
			.detail(orderId)
			.then((res) => {
				setOrder(res.data);
				setDraft(toDraft(res.data));
			})
			.catch((e) => setError(formatError(e)));
	}, [orderId]);

	async function save() {
		if (!order || !draft) return;
		const payload: Record<string, unknown> = {
			customer_name: draft.customer_name,
			customer_phone: draft.customer_phone,
			customer_address: draft.customer_address,
			advance_paid: draft.advance_paid,
			tracking_number: draft.tracking_number,
			internal_note: draft.internal_note,
		};
		if (order.items.some((i) => draft.quantities[i.id] !== i.quantity)) {
			payload.items = order.items.map((i) => ({
				product_id: i.product_id,
				variant_id: i.variant_id,
				quantity: draft.quantities[i.id] ?? i.quantity,
			}));
		}
		if (draft.status !== order.status) payload.status = draft.status;
		setBusy(true);
		setError(null);
		try {
			const res = await orderApi.patch(order.id, payload);
			setOrder(res.data);
			setDraft(toDraft(res.data));
			onChanged();
		} catch (e) {
			setError(formatError(e));
		} finally {
			setBusy(false);
		}
	}

	const field = (key: keyof Omit<Draft, "quantities">, label: string) =>
		draft && (
			<div className="space-y-1">
				<Label htmlFor={key}>{label}</Label>
				<Input id={key} value={draft[key]} onChange={(e) => setDraft({ ...draft, [key]: e.target.value })} />
			</div>
		);

	return (
		<Dialog open={orderId !== null} onOpenChange={onOpenChange}>
			<DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-xl">
				<DialogHeader>
					<DialogTitle>{order ? `Order ${order.number ?? "(draft)"}` : "Order"}</DialogTitle>
					<DialogDescription>{order ? `${order.channel} · ${order.status}${order.is_test ? " · test" : ""}` : ""}</DialogDescription>
				</DialogHeader>
				{error && (
					<Alert variant="destructive">
						<AlertDescription>{error}</AlertDescription>
					</Alert>
				)}
				{order && draft && (
					<div className="space-y-5 text-sm">
						<section className="space-y-2">
							<h3 className="font-medium">Items</h3>
							{order.items.map((item) => (
								<div key={item.id} className="flex items-center gap-2">
									<span className="flex-1">
										{item.name}
										{item.variant_label ? ` (${item.variant_label})` : ""}
									</span>
									<Input
										type="number"
										min={1}
										max={100}
										className="w-20"
										aria-label={`Quantity of ${item.name}`}
										value={draft.quantities[item.id]}
										onChange={(e) =>
											setDraft({ ...draft, quantities: { ...draft.quantities, [item.id]: Number(e.target.value) } })
										}
									/>
									<span className="w-24 text-right">
										{currencySymbol(order.currency)}
										{item.line_total}
									</span>
								</div>
							))}
							<div className="text-right text-muted-foreground">
								Subtotal {currencySymbol(order.currency)}
								{order.subtotal} · Delivery {currencySymbol(order.currency)}
								{order.zone_charge} · <span className="font-medium text-foreground">Total {currencySymbol(order.currency)}{order.total}</span>
							</div>
						</section>

						<section className="grid gap-3 sm:grid-cols-2">
							{field("customer_name", "Customer name")}
							{field("customer_phone", "Phone")}
							<div className="sm:col-span-2">{field("customer_address", "Address")}</div>
							{field("advance_paid", "Advance paid")}
							{field("tracking_number", "Tracking number")}
							<div className="space-y-1">
								<Label htmlFor="status">Status</Label>
								<select
									id="status"
									className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
									value={draft.status}
									onChange={(e) => setDraft({ ...draft, status: e.target.value })}
								>
									{[order.status, ...STATUSES.filter((s) => s !== order.status)].map((s) => (
										<option key={s} value={s}>
											{s}
										</option>
									))}
								</select>
							</div>
							<div className="sm:col-span-2">{field("internal_note", "Internal note")}</div>
						</section>

						<section className="space-y-1">
							<h3 className="font-medium">History</h3>
							{order.history.length === 0 && <p className="text-muted-foreground">No changes yet.</p>}
							{order.history.map((h, i) => (
								<p key={i} className="text-muted-foreground">
									{new Date(h.created_at).toLocaleString()} · {h.from_status} → {h.to_status} · {h.actor_type}
								</p>
							))}
						</section>

						<section className="space-y-1">
							<h3 className="font-medium">Actions</h3>
							<p className="text-muted-foreground">No actions have run for this order.</p>
						</section>

						<div className="flex items-center justify-between">
							{order.conversation_id ? (
								<Link href={`/conversations/${order.conversation_id}`} className="text-primary underline">
									Open the chat
								</Link>
							) : (
								<span />
							)}
							<Button onClick={save} disabled={busy}>
								{busy ? "Saving…" : "Save"}
							</Button>
						</div>
					</div>
				)}
			</DialogContent>
		</Dialog>
	);
}
