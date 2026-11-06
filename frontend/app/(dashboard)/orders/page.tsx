"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import RequirementsDialog from "@/components/orders/RequirementsDialog";
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
	const [dialogOpen, setDialogOpen] = useState(false);
	const [busy, setBusy] = useState(false);
	const [filters, setFilters] = useState<OrderFilters>({});
	const [page, setPage] = useState(1);
	const [orders, setOrders] = useState<OrderRow[]>([]);
	const [count, setCount] = useState(0);
	const [selected, setSelected] = useState<Set<string>>(new Set());
	const [openId, setOpenId] = useState<string | null>(null);

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
			setError(formatError(e));
		}
	}, [project, filters, page]);

	useEffect(() => {
		load();
	}, [load]);

	useEffect(() => {
		loadOrders();
	}, [loadOrders]);

	async function turnOff() {
		if (!project) return;
		setBusy(true);
		try {
			await orderApi.disable(String(project.id));
			await load();
		} catch (e) {
			setError(formatError(e));
		} finally {
			setBusy(false);
		}
	}

	async function tryIt() {
		if (!project) return;
		try {
			await orderApi.setTestMode(String(project.id), true);
			const res = await conversationApi.create({ project_slug: project.slug });
			router.push(`/conversations/${res.data.conversation.id}`);
		} catch (e) {
			setError(formatError(e));
		}
	}

	async function applyStatus(status: string) {
		setBusy(true);
		try {
			await orderApi.bulkStatus([...selected], status);
			setSelected(new Set());
			await loadOrders();
		} catch (e) {
			setError(formatError(e));
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

	return (
		<div className="mx-auto max-w-4xl space-y-6">
			<h1 className="text-2xl font-semibold">Orders</h1>
			{error && (
				<Alert variant="destructive">
					<AlertDescription>{error}</AlertDescription>
				</Alert>
			)}
			{settings.enabled ? (
				<Card>
					<CardHeader>
						<CardTitle>Taking orders</CardTitle>
						<CardDescription>The agent takes orders on the channels you chose.</CardDescription>
					</CardHeader>
					<CardContent className="flex gap-2">
						{isAdmin && (
							<Button variant="outline" onClick={turnOff} disabled={busy}>
								Turn off
							</Button>
						)}
						{isAdmin && (
							<Button variant="ghost" onClick={() => router.push("/project-settings/orders")}>
								Order settings
							</Button>
						)}
					</CardContent>
				</Card>
			) : (
				isAdmin && (
					<Card>
						<CardHeader>
							<CardTitle>Start taking orders</CardTitle>
							<CardDescription>The agent collects an order in the customer&apos;s language and confirms it with you.</CardDescription>
						</CardHeader>
						<CardContent>
							<Button onClick={() => setDialogOpen(true)}>Start taking orders</Button>
						</CardContent>
					</Card>
				)
			)}

			<section className="space-y-3">
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
			</section>

			<RequirementsDialog
				open={dialogOpen}
				onOpenChange={setDialogOpen}
				projectId={String(project.id)}
				onContinue={() => router.push("/orders/setup")}
				onTryIt={tryIt}
			/>
			<OrderDrawer orderId={openId} onOpenChange={(open) => !open && setOpenId(null)} onChanged={loadOrders} />
		</div>
	);
}
