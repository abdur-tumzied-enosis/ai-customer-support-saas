"use client";

import { useEffect, useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { orderApi, type LiveHeader, type LiveLookup } from "@/lib/orders-api";
import { formatError } from "@/lib/errors";

const MAX_HEADERS = 5;

export default function LiveLookupSection({ projectId }: { projectId: string }) {
	const [draft, setDraft] = useState<LiveLookup | null>(null);
	const [headers, setHeaders] = useState<LiveHeader[]>([]);
	const [busy, setBusy] = useState(false);
	const [notice, setNotice] = useState<{ type: "ok" | "error"; text: string } | null>(null);

	// Stored header values never come back: a blank value keeps the saved secret.
	const load = (config: LiveLookup) => {
		setDraft(config);
		setHeaders(config.headers.map((h) => ({ name: h.name, value: "", has_value: !!h.has_value })));
	};

	useEffect(() => {
		orderApi
			.settings(projectId)
			.then((r) => load(r.data.live_lookup))
			.catch((e) => setNotice({ type: "error", text: formatError(e) }));
	}, [projectId]);

	if (!draft) return <p className="text-sm text-muted-foreground">{notice ? notice.text : "Loading…"}</p>;

	const set = (patch: Partial<LiveLookup>) => setDraft({ ...draft, ...patch });
	const setHeader = (i: number, patch: Partial<LiveHeader>) =>
		setHeaders(headers.map((h, n) => (n === i ? { ...h, ...patch } : h)));

	async function save() {
		setBusy(true);
		setNotice(null);
		try {
			const payload = {
				...draft,
				headers: headers.filter((h) => h.name.trim()).map((h) => ({ name: h.name.trim(), value: h.value ?? "" })),
			};
			const r = await orderApi.saveSettings(projectId, { live_lookup: payload as LiveLookup });
			load(r.data.live_lookup);
			setNotice({ type: "ok", text: "Live lookup saved" });
		} catch (e) {
			setNotice({ type: "error", text: formatError(e) });
		} finally {
			setBusy(false);
		}
	}

	return (
		<div className="space-y-4 text-sm">
			<label className="flex items-center gap-2">
				<input type="checkbox" checked={draft.enabled} onChange={(e) => set({ enabled: e.target.checked })} />
				Use our API for prices and availability (checked on each product lookup and again at submit)
			</label>
			<div className="space-y-1">
				<Label htmlFor="live-url">Lookup URL</Label>
				<Input
					id="live-url"
					placeholder="https://api.example.com/products/{{product.external_id}}"
					value={draft.url_template}
					onChange={(e) => set({ url_template: e.target.value })}
				/>
				<p className="text-muted-foreground">Use {"{{product.external_id}}"} or {"{{product.sku}}"}. The value is URL-encoded.</p>
			</div>
			<div className="grid gap-3 sm:grid-cols-2">
				<div className="space-y-1">
					<Label htmlFor="live-price">Price field in the answer</Label>
					<Input id="live-price" placeholder="data.price" value={draft.price_path} onChange={(e) => set({ price_path: e.target.value })} />
				</div>
				<div className="space-y-1">
					<Label htmlFor="live-available">Availability field (optional)</Label>
					<Input
						id="live-available"
						placeholder="data.in_stock"
						value={draft.available_path ?? ""}
						onChange={(e) => set({ available_path: e.target.value || null })}
					/>
				</div>
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
				<Button
					variant="outline"
					size="sm"
					disabled={headers.length >= MAX_HEADERS}
					onClick={() => setHeaders([...headers, { name: "", value: "", has_value: false }])}
				>
					<Plus className="mr-1 h-4 w-4" /> Add header
				</Button>
			</div>
			{notice && <p className={notice.type === "ok" ? "text-green-700" : "text-destructive"}>{notice.text}</p>}
			<Button onClick={save} disabled={busy}>
				{busy ? "Saving…" : "Save live lookup"}
			</Button>
		</div>
	);
}
