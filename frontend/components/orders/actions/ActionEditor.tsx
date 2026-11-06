"use client";

import { useEffect, useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { actionApi, type ActionHeader, type OrderAction } from "@/lib/orders-api";
import { formatError } from "@/lib/errors";

const MAX_HEADERS = 5;
const STATUSES = ["new", "confirmed", "shipped", "delivered", "cancelled"];
const PLACEHOLDERS = [
	"order.number", "order.total", "order.subtotal", "order.zone_charge", "order.advance_paid", "order.collect_amount",
	"order.status", "order.tracking_number", "order.currency", "order.items", "customer.name", "customer.phone",
	"customer.address", "customer.note", "zone.name",
];

type Form = Omit<OrderAction, "id" | "headers" | "response_mapping"> & { tracking_path: string };

function toForm(a: OrderAction | null): Form {
	return a
		? { ...a, tracking_path: a.response_mapping.tracking_number ?? "" }
		: {
				name: "", method: "POST", url: "", body_template: "", success_status: "", success_codes: [200, 201, 202, 204],
				auto_run: false, is_active: true, tracking_path: "",
			};
}

type Props = {
	projectId: string;
	action: OrderAction | null;
	open: boolean;
	onOpenChange: (open: boolean) => void;
	onSaved: () => void;
};

export default function ActionEditor({ projectId, action, open, onOpenChange, onSaved }: Props) {
	const [form, setForm] = useState<Form>(toForm(action));
	const [headers, setHeaders] = useState<ActionHeader[]>([]);
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const [testNumber, setTestNumber] = useState("");
	const [testResult, setTestResult] = useState<string | null>(null);

	useEffect(() => {
		if (!open) return;
		setForm(toForm(action));
		setHeaders((action?.headers ?? []).map((h) => ({ name: h.name, value: "", has_value: !!h.has_value })));
		setError(null);
		setTestResult(null);
	}, [open, action]);

	const set = (patch: Partial<Form>) => setForm({ ...form, ...patch });
	const setHeader = (i: number, patch: Partial<ActionHeader>) =>
		setHeaders(headers.map((h, n) => (n === i ? { ...h, ...patch } : h)));

	async function save() {
		setBusy(true);
		setError(null);
		const { tracking_path, ...rest } = form;
		const payload: Partial<OrderAction> = {
			...rest,
			body_template: form.method === "POST" ? form.body_template : "",
			response_mapping: tracking_path.trim() ? { tracking_number: tracking_path.trim() } : {},
			headers: headers.filter((h) => h.name.trim()).map((h) => ({ name: h.name.trim(), value: h.value ?? "" })) as ActionHeader[],
		};
		try {
			if (action) await actionApi.update(action.id, projectId, payload);
			else await actionApi.create(projectId, payload);
			onSaved();
			onOpenChange(false);
		} catch (e) {
			setError(formatError(e));
		} finally {
			setBusy(false);
		}
	}

	async function test() {
		if (!action) return;
		setTestResult(null);
		try {
			const r = await actionApi.test(action.id, projectId, Number(testNumber));
			setTestResult(`${r.data.ok ? "Sent" : "Failed"}${r.data.http_status ? ` · HTTP ${r.data.http_status}` : ""}${r.data.error ? ` · ${r.data.error}` : ""}\n${r.data.response_excerpt}`);
		} catch (e) {
			setTestResult(formatError(e));
		}
	}

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
				<DialogHeader>
					<DialogTitle>{action ? `Edit ${action.name}` : "New action"}</DialogTitle>
					<DialogDescription>One request per order. Values in the body are JSON-encoded, values in the URL are URL-encoded.</DialogDescription>
				</DialogHeader>
				<div className="space-y-4 text-sm">
					<div className="grid gap-3 sm:grid-cols-[1fr_120px]">
						<div className="space-y-1">
							<Label htmlFor="action-name">Name</Label>
							<Input id="action-name" value={form.name} onChange={(e) => set({ name: e.target.value })} />
						</div>
						<div className="space-y-1">
							<Label htmlFor="action-method">Method</Label>
							<select
								id="action-method"
								className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
								value={form.method}
								onChange={(e) => set({ method: e.target.value as "GET" | "POST" })}
							>
								<option value="POST">POST</option>
								<option value="GET">GET</option>
							</select>
						</div>
					</div>
					<div className="space-y-1">
						<Label htmlFor="action-url">URL</Label>
						<Input id="action-url" placeholder="https://courier.example.com/orders" value={form.url} onChange={(e) => set({ url: e.target.value })} />
					</div>
					<div className="space-y-2">
						<p className="font-medium">Headers</p>
						<p className="text-muted-foreground">Values are write-only: once saved they are never shown again.</p>
						{headers.map((h, i) => (
							<div key={i} className="flex items-center gap-2">
								<Input aria-label="Header name" className="flex-1" value={h.name} onChange={(e) => setHeader(i, { name: e.target.value })} />
								<Input
									aria-label="Header value"
									type="password"
									className="flex-1"
									placeholder={h.has_value ? "Saved. Type to replace" : "Value"}
									value={h.value ?? ""}
									onChange={(e) => setHeader(i, { value: e.target.value })}
								/>
								<Button variant="ghost" size="icon" aria-label="Remove header" onClick={() => setHeaders(headers.filter((_, n) => n !== i))}>
									<Trash2 className="h-4 w-4" />
								</Button>
							</div>
						))}
						<Button variant="outline" size="sm" disabled={headers.length >= MAX_HEADERS} onClick={() => setHeaders([...headers, { name: "", value: "", has_value: false }])}>
							<Plus className="mr-1 h-4 w-4" /> Add header
						</Button>
					</div>
					{form.method === "POST" && (
						<div className="space-y-1">
							<Label htmlFor="action-body">Body (JSON)</Label>
							<Textarea
								id="action-body"
								rows={6}
								className="font-mono text-xs"
								placeholder={'{"order": {{ order.number }}, "name": {{ customer.name }}}'}
								value={form.body_template}
								onChange={(e) => set({ body_template: e.target.value })}
							/>
							<p className="text-muted-foreground">
								Write placeholders without quotes: {PLACEHOLDERS.map((p) => `{{ ${p} }}`).join(", ")}
							</p>
						</div>
					)}
					<div className="grid gap-3 sm:grid-cols-2">
						<div className="space-y-1">
							<Label htmlFor="action-tracking">Tracking number path (optional)</Label>
							<Input id="action-tracking" placeholder="data.tracking" value={form.tracking_path} onChange={(e) => set({ tracking_path: e.target.value })} />
						</div>
						<div className="space-y-1">
							<Label htmlFor="action-status">Set order status on success</Label>
							<select
								id="action-status"
								className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
								value={form.success_status}
								onChange={(e) => set({ success_status: e.target.value })}
							>
								<option value="">Leave as it is</option>
								{STATUSES.map((s) => (
									<option key={s} value={s}>
										{s}
									</option>
								))}
							</select>
						</div>
					</div>
					<div className="flex flex-wrap gap-6">
						<label className="flex items-center gap-2">
							<input type="checkbox" checked={form.is_active} onChange={(e) => set({ is_active: e.target.checked, auto_run: e.target.checked ? form.auto_run : false })} />
							Active
						</label>
						<label className="flex items-center gap-2">
							<input type="checkbox" checked={form.auto_run} disabled={!form.is_active} onChange={(e) => set({ auto_run: e.target.checked })} />
							Run automatically on new orders
						</label>
					</div>
					{error && <p className="text-destructive">{error}</p>}
					<div className="flex justify-end">
						<Button onClick={save} disabled={busy}>
							{busy ? "Saving…" : "Save action"}
						</Button>
					</div>
					{action && (
						<div className="space-y-2 border-t pt-4">
							<p className="font-medium">Test with an order</p>
							<div className="flex items-center gap-2">
								<Input aria-label="Order number" className="w-32" placeholder="Order #" value={testNumber} onChange={(e) => setTestNumber(e.target.value)} />
								<Button variant="outline" onClick={test} disabled={!testNumber.trim()}>
									Test
								</Button>
							</div>
							{testResult && <pre className="whitespace-pre-wrap rounded bg-muted p-2 text-xs">{testResult}</pre>}
						</div>
					)}
				</div>
			</DialogContent>
		</Dialog>
	);
}
