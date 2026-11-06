"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { orderApi, type OrderRow } from "@/lib/orders-api";
import { formatError } from "@/lib/errors";

type Props = { projectId: string; conversationId: string; version: number };

export default function ConversationOrders({ projectId, conversationId, version }: Props) {
	const [orders, setOrders] = useState<OrderRow[]>([]);
	const [error, setError] = useState<string | null>(null);

	useEffect(() => {
		orderApi
			.list(projectId, { conversation_id: conversationId })
			.then((res) => setOrders(res.data.orders))
			.catch((e) => setError(formatError(e)));
	}, [projectId, conversationId, version]);

	if (error) return <p className="px-4 py-2 text-sm text-destructive">{error}</p>;
	if (orders.length === 0) return null;
	return (
		<div className="mx-4 mt-3 flex flex-wrap items-center gap-2 text-sm">
			<span className="text-muted-foreground">Orders from this chat:</span>
			{orders.map((o) => (
				<Badge key={o.id} variant="secondary">
					#{o.number ?? "draft"} · {o.status} · {o.total}
				</Badge>
			))}
		</div>
	);
}
