"use client";

import { useEffect, useState } from "react";
import { billingApi, formatCredits, type CreditRequest, type PublicPricing } from "@/lib/billing-api";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

type Pkg = PublicPricing["packages"][number];

export default function CreditRequestDialog({ projectId }: { projectId: string }) {
	const [open, setOpen] = useState(false);
	const [packages, setPackages] = useState<Pkg[]>([]);
	const [pkgId, setPkgId] = useState<number | null>(null);
	const [phone, setPhone] = useState("");
	const [req, setReq] = useState<CreditRequest | null>(null);
	const [trx, setTrx] = useState("");
	const [from, setFrom] = useState("");
	const [done, setDone] = useState(false);
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState("");

	useEffect(() => {
		if (open && !packages.length) billingApi.pricing().then((d) => setPackages(d.packages)).catch(() => setError("Could not load packages."));
	}, [open, packages.length]);

	const run = async (fn: () => Promise<void>) => {
		setBusy(true);
		setError("");
		try {
			await fn();
		} catch (e) {
			setError(e instanceof Error ? e.message : "Something went wrong. Try again.");
		} finally {
			setBusy(false);
		}
	};

	const onOpenChange = (v: boolean) => {
		setOpen(v);
		if (!v) {
			setReq(null);
			setDone(false);
			setTrx("");
			setFrom("");
			setError("");
		}
	};

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogTrigger asChild>
				<Button>Request credits</Button>
			</DialogTrigger>
			<DialogContent>
				<DialogHeader>
					<DialogTitle>Request credits</DialogTitle>
					<DialogDescription>Pay with bKash and we will add the credits to your project.</DialogDescription>
				</DialogHeader>

				{done ? (
					<p className="text-sm">Thanks! We received your payment details and will add your credits after we confirm the payment.</p>
				) : !req ? (
					<form
						className="space-y-4"
						onSubmit={(e) => {
							e.preventDefault();
							if (pkgId) run(async () => setReq(await billingApi.requestCredits(projectId, pkgId, phone)));
						}}
					>
						<div className="space-y-2" role="radiogroup" aria-label="Package">
							{packages.map((p) => (
								<label key={p.id} className="flex cursor-pointer items-center justify-between gap-3 rounded-lg border p-3 text-sm transition-colors hover:bg-muted/50 has-[:checked]:border-primary has-[:checked]:bg-primary/5 has-[:checked]:ring-1 has-[:checked]:ring-primary">
									<span className="flex items-center gap-2">
										<input type="radio" name="pkg" checked={pkgId === p.id} onChange={() => setPkgId(p.id)} />
										<span className="font-medium">{p.name}</span>
										<span className="text-muted-foreground">{formatCredits(p.credits + p.bonus_credits)} credits</span>
									</span>
									<span className="tabular-nums">৳{formatCredits(p.price)}</span>
								</label>
							))}
						</div>
						<div className="space-y-2">
							<Label htmlFor="cr-phone">Your phone number</Label>
							<Input id="cr-phone" type="tel" required minLength={6} maxLength={20} value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="01XXXXXXXXX" />
						</div>
						{error && <p className="text-sm text-destructive">{error}</p>}
						<Button type="submit" disabled={busy || !pkgId || phone.trim().length < 6}>
							Submit request
						</Button>
					</form>
				) : (
					<form
						className="space-y-4"
						onSubmit={(e) => {
							e.preventDefault();
							run(async () => {
								await billingApi.submitPayment(projectId, req.id, trx, from);
								setDone(true);
							});
						}}
					>
						<ol className="list-decimal space-y-2 rounded-lg bg-muted/50 p-4 pl-9 text-sm">
							<li>
								Send <b>৳{formatCredits(req.amount)}</b> by bKash (Send Money) to <b className="select-all">{req.bkash_number || "(number not set)"}</b>.
							</li>
							<li>
								Add <b className="select-all">{req.reference}</b> as the reference.
							</li>
							<li>When the payment is done, enter the transaction ID below.</li>
						</ol>
						<div className="space-y-2">
							<Label htmlFor="cr-trx">bKash transaction ID</Label>
							<Input id="cr-trx" required maxLength={40} value={trx} onChange={(e) => setTrx(e.target.value)} />
						</div>
						<div className="space-y-2">
							<Label htmlFor="cr-from">bKash number you sent from</Label>
							<Input id="cr-from" type="tel" required minLength={6} maxLength={20} value={from} onChange={(e) => setFrom(e.target.value)} placeholder="01XXXXXXXXX" />
						</div>
						{error && <p className="text-sm text-destructive">{error}</p>}
						<Button type="submit" disabled={busy || !trx.trim() || from.trim().length < 6}>
							Submit payment details
						</Button>
					</form>
				)}
			</DialogContent>
		</Dialog>
	);
}
