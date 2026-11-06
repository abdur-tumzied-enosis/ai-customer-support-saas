"use client";

import { Badge } from "@/components/ui/badge";
import { currencySymbol } from "@/components/knowledge/catalog/ProductTable";
import type { OrderRow } from "@/lib/orders-api";

type Props = {
	orders: OrderRow[];
	selected: Set<string>;
	onToggle: (id: string) => void;
	onToggleAll: () => void;
	onOpen: (id: string) => void;
};

export default function OrdersTable({ orders, selected, onToggle, onToggleAll, onOpen }: Props) {
	if (orders.length === 0) {
		return <p className="text-sm text-muted-foreground">No orders match these filters.</p>;
	}
	return (
		<div className="overflow-x-auto rounded-md border">
			<table className="w-full text-sm">
				<thead className="bg-muted text-left">
					<tr>
						<th className="p-2">
							<input
								type="checkbox"
								aria-label="Select all"
								checked={selected.size === orders.length}
								onChange={onToggleAll}
							/>
						</th>
						<th className="p-2">No.</th>
						<th className="p-2">Date</th>
						<th className="p-2">Customer</th>
						<th className="p-2">Status</th>
						<th className="p-2 text-right">Total</th>
						<th className="p-2 text-right">To collect</th>
						<th className="p-2">Channel</th>
					</tr>
				</thead>
				<tbody>
					{orders.map((o) => (
						<tr key={o.id} className="cursor-pointer border-t hover:bg-muted/50" onClick={() => onOpen(o.id)}>
							<td className="p-2" onClick={(e) => e.stopPropagation()}>
								<input
									type="checkbox"
									aria-label={`Select order ${o.number ?? "draft"}`}
									checked={selected.has(o.id)}
									onChange={() => onToggle(o.id)}
								/>
							</td>
							<td className="p-2">{o.number ?? "—"}</td>
							<td className="p-2">{new Date(o.created_at).toLocaleDateString()}</td>
							<td className="p-2">{o.customer_name || "—"}</td>
							<td className="p-2">
								<Badge variant="secondary">{o.status}</Badge>
								{o.is_test && <Badge variant="outline" className="ml-1">test</Badge>}
							</td>
							<td className="p-2 text-right">
								{currencySymbol(o.currency)}
								{o.total}
							</td>
							<td className="p-2 text-right">
								{currencySymbol(o.currency)}
								{o.collect_amount}
							</td>
							<td className="p-2">{o.channel}</td>
						</tr>
					))}
				</tbody>
			</table>
		</div>
	);
}
