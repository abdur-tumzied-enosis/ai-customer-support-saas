"use client";

import { useEffect, useState } from "react";
import { Loader2, Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { useToast } from "@/components/ui/toast";
import { orderApi, type OrderSettings, type OrderZone, type PaymentMethod } from "@/lib/orders-api";
import { formatError } from "@/lib/errors";
import { cn } from "@/lib/utils";
import { useSetupDirty } from "@/components/orders/setup/SetupDirty";
import { ORDERS_CHANGED } from "@/components/orders/OrdersShell";

const CHANNELS = [
	{ value: "facebook", label: "Facebook Messenger" },
	{ value: "instagram", label: "Instagram" },
	{ value: "whatsapp", label: "WhatsApp" },
	{ value: "widget", label: "Website widget" },
];

type Props = { projectId: string; onSaved: () => void; saveLabel?: string };

export default function SetupStepSettings({ projectId, onSaved, saveLabel = "Save and continue" }: Props) {
	const [draft, setDraft] = useState<OrderSettings | null>(null);
	const [error, setError] = useState<string | null>(null);
	const toast = useToast();
	const [busy, setBusy] = useState(false);
	const [triedSave, setTriedSave] = useState(false);
	const [saved, setSaved] = useState("");
	const setDirty = useSetupDirty();
	const dirty = draft !== null && JSON.stringify(draft) !== saved;

	useEffect(() => {
		orderApi
			.settings(projectId)
			.then((res) => {
				setDraft(res.data);
				setSaved(JSON.stringify(res.data));
			})
			.catch((e) => setError(formatError(e)));
	}, [projectId]);

	useEffect(() => {
		setDirty(dirty);
	}, [dirty, setDirty]);

	useEffect(() => () => setDirty(false), [setDirty]);

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
		setTriedSave(true);
		const bad = draft!.zones.findIndex((z) => !z.name.trim() || !z.charge);
		if (bad >= 0) {
			toast.error(
				`Delivery zone ${bad + 1} is missing a name or a charge. Fill in both boxes in the "Delivery zones and charges" section below (the red boxes), or remove that row with the trash icon. Orders can't go live until every zone is complete.`,
			);
			return;
		}
		setBusy(true);
		try {
			await orderApi.saveSettings(projectId, {
				channels: draft!.channels,
				zones: draft!.zones,
				payment_methods: draft!.payment_methods,
				notification_emails: draft!.notification_emails,
			});
			setSaved(JSON.stringify(draft));
			window.dispatchEvent(new Event(ORDERS_CHANGED));
			onSaved();
		} catch (e) {
			toast.error(formatError(e));
		} finally {
			setBusy(false);
		}
	}

	return (
		<div className="space-y-6">

			<section className="space-y-2 pb-2">
				<Label>Where orders can come from</Label>
				<p className="text-sm text-muted-foreground">
					Tick the chats where customers can place orders. The agent only takes orders on the ticked ones. To connect a chat first, go to Channels.
				</p>
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

			<section className="space-y-3 rounded-lg border-2 border-primary/50 bg-primary/5 p-4">
				<div>
					<Label className="text-base font-semibold">Delivery zones and charges</Label>
					<p className="text-sm text-muted-foreground">
						Required. One row per area you deliver to: the area name on the left, its delivery fee (a number) on the right. Enter 0 for free delivery.
					</p>
				</div>
				{draft.zones.length > 0 && (
					<div className="grid grid-cols-[1fr_9rem_2.5rem] gap-2 text-xs font-medium text-muted-foreground">
						<span>Area name</span>
						<span>Delivery fee</span>
						<span />
					</div>
				)}
				{draft.zones.map((zone, i) => (
					<div key={i} className="grid grid-cols-[1fr_9rem_2.5rem] gap-2">
						<Input
							placeholder="e.g. Inside city"
							aria-label={`Zone ${i + 1} name`}
							className={triedSave && !zone.name.trim() ? "border-destructive" : undefined}
							value={zone.name}
							onChange={(e) => setZone(i, { name: e.target.value })}
						/>
						<Input
							type="number"
							inputMode="decimal"
							min="0"
							step="0.01"
							placeholder="0.00"
							aria-label={`Zone ${i + 1} delivery fee`}
							className={cn("text-right tabular-nums", triedSave && !zone.charge && "border-destructive")}
							value={zone.charge ?? ""}
							onChange={(e) => setZone(i, { charge: e.target.value || null })}
						/>
						<Button variant="ghost" size="icon" aria-label={`Remove zone ${i + 1}`} onClick={() => update({ zones: draft.zones.filter((_, j) => j !== i) })}>
							<Trash2 className="h-4 w-4" />
						</Button>
					</div>
				))}
				<Button variant="outline" size="sm" onClick={() => update({ zones: [...draft.zones, { name: "", charge: null }] })}>
					<Plus className="mr-1 h-4 w-4" /> Add zone
				</Button>
			</section>

			<section className="space-y-2 border-t pt-6">
				<Label>Payment methods (pick one default)</Label>
				<p className="text-sm text-muted-foreground">
					The ways customers can pay. Click the circle next to the one to offer first. Use the trash icon to remove a method you don&apos;t accept.
				</p>
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

			<section className="space-y-2 border-t pt-6">
				<Label htmlFor="emails">Notification emails (comma separated, up to 10)</Label>
				<p className="text-sm text-muted-foreground">
					Each new order is emailed to these addresses. Separate them with commas, for example &quot;sales@shop.com, owner@shop.com&quot;.
				</p>
				<Input
					id="emails"
					value={draft.notification_emails.join(", ")}
					onChange={(e) =>
						update({ notification_emails: e.target.value.split(",").map((v) => v.trim()).filter(Boolean) })
					}
				/>
			</section>

			<div className="sticky bottom-0 -mx-1 flex justify-end border-t bg-background/95 px-1 py-3 backdrop-blur">
				<Button onClick={save} disabled={busy}>
					{busy ? "Saving…" : saveLabel}
				</Button>
			</div>
		</div>
	);
}
