"use client";

import { useEffect, useState } from "react";
import { Loader2, Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { orderApi, type OrderSettings, type OrderZone, type PaymentMethod } from "@/lib/orders-api";
import { formatError } from "@/lib/errors";

const CHANNELS = [
	{ value: "facebook", label: "Facebook Messenger" },
	{ value: "instagram", label: "Instagram" },
	{ value: "whatsapp", label: "WhatsApp" },
	{ value: "widget", label: "Website widget" },
];

type Props = { projectId: string; onSaved: () => void };

export default function SetupStepSettings({ projectId, onSaved }: Props) {
	const [draft, setDraft] = useState<OrderSettings | null>(null);
	const [error, setError] = useState<string | null>(null);
	const [busy, setBusy] = useState(false);

	useEffect(() => {
		orderApi.settings(projectId).then((res) => setDraft(res.data)).catch((e) => setError(formatError(e)));
	}, [projectId]);

	if (!draft) {
		return error ? <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert> : <Loader2 className="h-5 w-5 animate-spin" />;
	}

	const update = (patch: Partial<OrderSettings>) => setDraft({ ...draft, ...patch });

	function setZone(index: number, patch: Partial<OrderZone>) {
		update({ zones: draft!.zones.map((z, i) => (i === index ? { ...z, ...patch } : z)) });
	}

	function setPayment(index: number, patch: Partial<PaymentMethod>) {
		update({ payment_methods: draft!.payment_methods.map((p, i) => (i === index ? { ...p, ...patch } : p)) });
	}

	function chooseDefault(index: number) {
		update({ payment_methods: draft!.payment_methods.map((p, i) => ({ ...p, default: i === index })) });
	}

	async function save() {
		if (draft!.zones.some((z) => !z.name.trim() || !z.charge)) {
			setError("Every delivery zone needs a name and a delivery charge before you go live.");
			return;
		}
		setBusy(true);
		setError(null);
		try {
			await orderApi.saveSettings(projectId, {
				channels: draft!.channels,
				zones: draft!.zones,
				payment_methods: draft!.payment_methods,
				form_fields: draft!.form_fields,
				notification_emails: draft!.notification_emails,
			});
			onSaved();
		} catch (e) {
			setError(formatError(e));
		} finally {
			setBusy(false);
		}
	}

	return (
		<div className="space-y-6">
			<h2 className="text-lg font-medium">Settings</h2>
			{error && (
				<Alert variant="destructive">
					<AlertDescription>{error}</AlertDescription>
				</Alert>
			)}

			<section className="space-y-2">
				<Label>Where orders can come from</Label>
				{CHANNELS.map((c) => (
					<label key={c.value} className="flex items-center gap-2 text-sm">
						<input
							type="checkbox"
							checked={draft.channels.includes(c.value)}
							onChange={(e) =>
								update({
									channels: e.target.checked
										? [...draft.channels, c.value]
										: draft.channels.filter((v) => v !== c.value),
								})
							}
						/>
						{c.label}
					</label>
				))}
			</section>

			<section className="space-y-2">
				<Label>Delivery zones and charges</Label>
				{draft.zones.map((zone, i) => (
					<div key={i} className="flex gap-2">
						<Input placeholder="Zone" value={zone.name} onChange={(e) => setZone(i, { name: e.target.value })} />
						<Input placeholder="Charge" value={zone.charge ?? ""} onChange={(e) => setZone(i, { charge: e.target.value || null })} />
						<Button variant="ghost" size="icon" onClick={() => update({ zones: draft.zones.filter((_, j) => j !== i) })}>
							<Trash2 className="h-4 w-4" />
						</Button>
					</div>
				))}
				<Button variant="outline" size="sm" onClick={() => update({ zones: [...draft.zones, { name: "", charge: null }] })}>
					<Plus className="mr-1 h-4 w-4" /> Add zone
				</Button>
			</section>

			<section className="space-y-2">
				<Label>Payment methods (pick one default)</Label>
				{draft.payment_methods.map((method, i) => (
					<div key={i} className="flex items-center gap-2">
						<input type="radio" name="default-payment" checked={method.default} onChange={() => chooseDefault(i)} />
						<Input value={method.label} onChange={(e) => setPayment(i, { label: e.target.value })} />
						<Button variant="ghost" size="icon" onClick={() => update({ payment_methods: draft.payment_methods.filter((_, j) => j !== i) })}>
							<Trash2 className="h-4 w-4" />
						</Button>
					</div>
				))}
				<Button
					variant="outline"
					size="sm"
					onClick={() =>
						update({ payment_methods: [...draft.payment_methods, { label: "", default: draft.payment_methods.length === 0 }] })
					}
				>
					<Plus className="mr-1 h-4 w-4" /> Add payment method
				</Button>
			</section>

			<section className="space-y-2">
				<Label htmlFor="emails">Notification emails (comma separated, up to 10)</Label>
				<Input
					id="emails"
					value={draft.notification_emails.join(", ")}
					onChange={(e) =>
						update({ notification_emails: e.target.value.split(",").map((v) => v.trim()).filter(Boolean) })
					}
				/>
			</section>

			<div className="flex justify-end">
				<Button onClick={save} disabled={busy}>
					{busy ? "Saving…" : "Save and continue"}
				</Button>
			</div>
		</div>
	);
}
