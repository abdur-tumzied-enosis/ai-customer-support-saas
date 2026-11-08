"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { useToast } from "@/components/ui/toast";
import OrdersEntryPanel from "@/components/orders/OrdersEntryPanel";
import OrdersLiveStrip from "@/components/orders/OrdersLiveStrip";
import OrderFiltersBar from "@/components/orders/OrderFiltersBar";
import OrdersTable from "@/components/orders/OrdersTable";
import ActionBar from "@/components/orders/ActionBar";
import OrderDrawer from "@/components/orders/OrderDrawer";
import { conversationApi } from "@/lib/api";
import { orderApi, type OrderFilters, type OrderRow, type OrderSettings } from "@/lib/orders-api";
import { formatError } from "@/lib/errors";
import { useAuth } from "@/hooks/useAuth";

const PAGE_SIZE = 25;

export default function OrdersPage() {
	const router = useRouter();
	const { projects } = useAuth();
	const project = projects.length > 0 ? projects[0] : null;
	const [settings, setSettings] = useState<OrderSettings | null>(null);
	const [error, setError] = useState<string | null>(null);
	const [busy, setBusy] = useState(false);
	const [filters, setFilters] = useState<OrderFilters>({});
	const [page, setPage] = useState(1);
	const [orders, setOrders] = useState<OrderRow[]>([]);
	const [count, setCount] = useState(0);
	const [selected, setSelected] = useState<Set<string>>(new Set());
	const [openId, setOpenId] = useState<string | null>(null);
	const toast = useToast();

	const load = useCallback(async () => {
		if (!project) return;
		try {
			setSettings((await orderApi.settings(String(project.id))).data);
		} catch (e) {
			setError(formatError(e));
		}
	}, [project]);

	const loadOrders = useCallback(async () => {
		if (!project) return;
		try {
			const res = await orderApi.list(String(project.id), filters, page);
			setOrders(res.data.orders);
			setCount(res.data.count);
		} catch (e) {
			toast.error(formatError(e));
		}
	}, [project, filters, page]);

	useEffect(() => {
		load();
	}, [load]);

	useEffect(() => {
		loadOrders();
	}, [loadOrders]);


	async function tryIt() {
		if (!project) return;
		try {
			// while ordering is on the test chat already takes orders; test mode is only for trying it before turning on
			if (!settings?.enabled) await orderApi.setTestMode(String(project.id), true);
			const res = await conversationApi.create({ project_slug: project.slug });
			router.push(`/conversations/${res.data.conversation.id}`);
		} catch (e) {
			toast.error(formatError(e));
		}
	}

	async function applyStatus(status: string) {
		setBusy(true);
		try {
			await orderApi.bulkStatus([...selected], status);
			setSelected(new Set());
			await loadOrders();
		} catch (e) {
			toast.error(formatError(e));
		} finally {
			setBusy(false);
		}
	}

	function toggle(id: string) {
		setSelected((prev) => {
			const next = new Set(prev);
			if (next.has(id)) next.delete(id);
			else next.add(id);
			return next;
		});
	}

	function toggleAll() {
		setSelected((prev) => (prev.size === orders.length ? new Set() : new Set(orders.map((o) => o.id))));
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

	const isAdmin = project.role === "admin";
	const lastPage = Math.max(1, Math.ceil(count / PAGE_SIZE));
	const hasFilters = Object.values(filters).some(Boolean);

	return (
		<div className="mx-auto max-w-4xl space-y-6">
			{!settings.enabled && isAdmin && (
				<OrdersEntryPanel projectId={String(project.id)} confirmed={settings.settings_confirmed_at !== null} onTryIt={tryIt} />
			)}
			{!settings.enabled && !isAdmin && count === 0 && <p className="text-sm text-muted-foreground">Orders are off. Ask an admin to turn them on.</p>}
			{settings.enabled && <OrdersLiveStrip channels={settings.channels} isAdmin={isAdmin} onOpenTestChat={tryIt} />}
			{settings.enabled && count === 0 && !hasFilters && (
				<p className="text-sm text-muted-foreground">New orders from your chats appear here.</p>
			)}

			{(settings.enabled || count > 0) && <section className="space-y-3">
				<div className="flex items-center justify-between">
					<h2 className="text-lg font-medium">Order list</h2>
					{isAdmin && (
						<a href={orderApi.exportUrl(String(project.id), filters)} className="text-sm text-primary underline">
							Export CSV
						</a>
					)}
				</div>
				<OrderFiltersBar
					value={filters}
					onChange={(next) => {
						setFilters(next);
						setPage(1);
						setSelected(new Set());
					}}
				/>
				<ActionBar
					count={selected.size}
					busy={busy}
					onApply={applyStatus}
					projectId={String(project.id)}
					ids={[...selected]}
					onRan={() => void loadOrders()}
				/>
				<OrdersTable orders={orders} selected={selected} onToggle={toggle} onToggleAll={toggleAll} onOpen={setOpenId} />
				<div className="flex items-center justify-between text-sm">
					<span>
						{count} orders · page {page} of {lastPage}
					</span>
					<div className="flex gap-2">
						<Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>
							Previous
						</Button>
						<Button variant="outline" size="sm" disabled={page >= lastPage} onClick={() => setPage(page + 1)}>
							Next
						</Button>
					</div>
				</div>
			</section>}

			<OrderDrawer orderId={openId} onOpenChange={(open) => !open && setOpenId(null)} onChanged={loadOrders} />
		</div>
	);
}
