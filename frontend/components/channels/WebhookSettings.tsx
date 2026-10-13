"use client";

import { useEffect, useState } from "react";
import { Copy, Eye, EyeOff, Loader2 } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { webhookApi, type ProjectWebhook } from "@/lib/api";
import { formatError } from "@/lib/errors";

export default function WebhookSettings({ projectSlug }: { projectSlug: string }) {
	const [hook, setHook] = useState<ProjectWebhook | null>(null);
	const [url, setUrl] = useState("");
	const [active, setActive] = useState(false);
	const [showSecret, setShowSecret] = useState(false);
	const [busy, setBusy] = useState(false);
	const [notice, setNotice] = useState<{ type: "ok" | "error"; text: string } | null>(null);

	useEffect(() => {
		webhookApi
			.get(projectSlug)
			.then((r) => {
				setHook(r.data.webhook);
				setUrl(r.data.webhook.url);
				setActive(r.data.webhook.is_active);
			})
			.catch((err) => setNotice({ type: "error", text: formatError(err) }));
	}, [projectSlug]);

	async function run(fn: () => Promise<void>) {
		setBusy(true);
		setNotice(null);
		try {
			await fn();
		} catch (err) {
			setNotice({ type: "error", text: formatError(err) });
		} finally {
			setBusy(false);
		}
	}

	const save = () =>
		run(async () => {
			const r = await webhookApi.update(projectSlug, { url: url.trim(), is_active: active });
			setHook(r.data.webhook);
			setNotice({ type: "ok", text: "Webhook saved" });
		});

	const rotate = () =>
		run(async () => {
			if (!confirm("Rotate the signing secret? Systems verifying the old secret will reject new events.")) return;
			const r = await webhookApi.rotate(projectSlug);
			setHook(r.data.webhook);
			setShowSecret(true);
			setNotice({ type: "ok", text: "Secret rotated" });
		});

	const sendTest = () =>
		run(async () => {
			const r = await webhookApi.test(projectSlug);
			setNotice({ type: "ok", text: `Test event delivered — your endpoint answered HTTP ${r.data.status}` });
		});

	return (
		<Card>
			<CardHeader>
				<CardTitle>Integrations — webhooks</CardTitle>
				<CardDescription>
					We POST signed JSON events to this URL: conversation.handoff, conversation.resumed and message.staff_reply.
					Verify the X-AMP-Signature header (HMAC-SHA256 of the raw body with the secret below).
				</CardDescription>
			</CardHeader>
			<CardContent className="space-y-4">
				{!hook ? (
					<Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
				) : (
					<>
						<div className="space-y-1">
							<Label htmlFor="webhook-url">Endpoint URL</Label>
							<Input
								id="webhook-url"
								placeholder="https://hooks.zapier.com/hooks/catch/…"
								value={url}
								onChange={(e) => setUrl(e.target.value)}
							/>
						</div>
						<label className="flex items-center gap-2 text-sm">
							<input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} />
							Send events
						</label>
						<div className="space-y-1">
							<Label>Signing secret</Label>
							<div className="flex gap-2">
								<Input readOnly value={showSecret ? hook.secret : "•".repeat(24)} className="font-mono" />
								<Button
									type="button"
									variant="outline"
									size="icon"
									aria-label={showSecret ? "Hide secret" : "Show secret"}
									onClick={() => setShowSecret((v) => !v)}
								>
									{showSecret ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
								</Button>
								<Button
									type="button"
									variant="outline"
									size="icon"
									aria-label="Copy secret"
									onClick={() => navigator.clipboard?.writeText(hook.secret)}
								>
									<Copy className="h-4 w-4" />
								</Button>
							</div>
						</div>
						<div className="flex flex-wrap gap-2">
							<Button onClick={save} disabled={busy}>Save</Button>
							<Button variant="outline" onClick={sendTest} disabled={busy || !hook.url}>Send test event</Button>
							<Button variant="ghost" onClick={rotate} disabled={busy}>Rotate secret</Button>
						</div>
						{notice && (
							<p className={notice.type === "ok" ? "text-sm text-green-600 dark:text-green-400" : "text-sm text-destructive"}>
								{notice.text}
							</p>
						)}
					</>
				)}
			</CardContent>
		</Card>
	);
}
