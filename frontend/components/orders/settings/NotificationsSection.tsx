"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { webhookApi } from "@/lib/api";
import { formatError } from "@/lib/errors";

// Same webhook record as the Channels page. The signing secret is shown there, never here.
export default function NotificationsSection({ projectSlug }: { projectSlug: string }) {
	const [url, setUrl] = useState("");
	const [active, setActive] = useState(false);
	const [loaded, setLoaded] = useState(false);
	const [busy, setBusy] = useState(false);
	const [notice, setNotice] = useState<{ type: "ok" | "error"; text: string } | null>(null);

	useEffect(() => {
		webhookApi
			.get(projectSlug)
			.then((r) => {
				setUrl(r.data.webhook.url);
				setActive(r.data.webhook.is_active);
				setLoaded(true);
			})
			.catch((e) => setNotice({ type: "error", text: formatError(e) }));
	}, [projectSlug]);

	async function save() {
		setBusy(true);
		setNotice(null);
		try {
			await webhookApi.update(projectSlug, { url: url.trim(), is_active: active });
			setNotice({ type: "ok", text: "Saved" });
		} catch (e) {
			setNotice({ type: "error", text: formatError(e) });
		} finally {
			setBusy(false);
		}
	}

	return (
		<div className="space-y-3 text-sm">
			<p className="font-medium">Send new orders to a URL</p>
			<p className="text-muted-foreground">
				We send each new order, with its products, as a signed POST to this URL. The same URL also receives every other
				project event (handoffs, staff replies, order status changes), so filter on the event field.
			</p>
			<div className="space-y-1">
				<Label htmlFor="order-webhook-url">Endpoint URL</Label>
				<Input
					id="order-webhook-url"
					placeholder="https://"
					value={url}
					disabled={!loaded}
					onChange={(e) => setUrl(e.target.value)}
				/>
			</div>
			<label className="flex items-center gap-2">
				<input type="checkbox" checked={active} disabled={!loaded} onChange={(e) => setActive(e.target.checked)} />
				Send events to this URL
			</label>
			{notice && <p className={notice.type === "ok" ? "text-green-700" : "text-destructive"}>{notice.text}</p>}
			<Button onClick={save} disabled={busy || !loaded}>
				{busy ? "Saving…" : "Save URL"}
			</Button>
		</div>
	);
}
