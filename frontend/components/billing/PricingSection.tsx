"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Check } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { billingApi, formatCredits, type PublicPricing } from "@/lib/billing-api";
import { site } from "@/lib/site";
import { cn } from "@/lib/utils";

const money = (n: number) => `৳${n.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
const perReplyText = (n: number) => `৳${n.toFixed(2)}`;

export default function PricingSection() {
	const [data, setData] = useState<PublicPricing | null>(null);
	const [state, setState] = useState<"loading" | "ok" | "error">("loading");

	useEffect(() => {
		billingApi
			.pricing()
			.then((d) => {
				setData(d);
				setState("ok");
			})
			.catch(() => setState("error"));
	}, []);

	if (state === "error" || (state === "ok" && !data?.packages.length)) return null;
	if (state === "loading" || !data) {
		return <section id="pricing" aria-hidden className="h-[64rem] md:h-[48rem]" />;
	}

	const replyCredits = Number(data.actions.find((a) => a.action === "reply")?.credits ?? 0);
	const rows = data.packages.map((p) => {
		const total = p.credits + p.bonus_credits;
		const replies = replyCredits > 0 ? total / replyCredits : 0;
		return { p, total, replies, perReply: replies > 0 ? Number(p.price) / replies : 0 };
	});
	const maxPerReply = Math.max(...rows.map((r) => r.perReply));
	const freeCredits = Number(data.signup_credits) > 0 ? data.signup_credits : null;

	return (
		<section id="pricing" className="py-20 px-6 scroll-mt-4">
			<div className="container mx-auto max-w-6xl">
				<h2 className="text-3xl font-bold text-center">Pay for the replies you send</h2>
				<p className="text-muted-foreground text-center mt-3 mb-12">
					Buy credits once and use them on every channel. Bigger packs cost less per reply.
				</p>

				<div className="grid grid-cols-1 md:grid-cols-3 gap-6">
					{rows.map(({ p, total, replies, perReply }) => {
						const muted = p.highlighted ? "text-primary-foreground/70" : "text-muted-foreground";
						return (
							<div
								key={p.name}
								className={cn(
									"rounded-lg border p-6 flex flex-col gap-4",
									p.highlighted ? "bg-primary text-primary-foreground border-primary" : "bg-card text-card-foreground border-border"
								)}
							>
								<div className="flex items-center justify-between">
									<h3 className="text-lg font-semibold">{p.name}</h3>
									{p.highlighted && <Badge variant="secondary">Most popular</Badge>}
								</div>
								{perReply > 0 && (
									<div>
										<p className="text-5xl font-bold tracking-tight">{perReplyText(perReply)}</p>
										<p className={cn("text-sm", muted)}>per AI reply</p>
									</div>
								)}
								<p className="text-sm">
									{money(Number(p.price))} for {formatCredits(total)} credits
								</p>
								{p.bonus_credits > 0 && (
									<p className="text-sm font-medium text-emerald-600 dark:text-emerald-400">
										+{formatCredits(p.bonus_credits)} bonus credits
									</p>
								)}
								{perReply > 0 && <p className={cn("text-sm", muted)}>About {formatCredits(Math.floor(replies))} AI replies*</p>}
								<ul className="space-y-2 text-sm flex-1">
									{p.features.map((f) => (
										<li key={f} className="flex gap-2">
											<Check className="h-4 w-4 mt-0.5 shrink-0" aria-hidden />
											<span>{f}</span>
										</li>
									))}
								</ul>
								<Button asChild variant={p.highlighted ? "secondary" : "outline"} className="w-full">
									<Link href="/sign-up">Get started</Link>
								</Button>
							</div>
						);
					})}
				</div>

				{maxPerReply > 0 && (
					<div
						className="mt-10 max-w-2xl mx-auto space-y-3"
						role="img"
						aria-label={`Price per AI reply: ${rows.map((r) => `${r.p.name} ${perReplyText(r.perReply)}`).join(", ")}`}
					>
						{rows.map(({ p, perReply }) => (
							<div key={p.name} className="flex items-center gap-3 text-sm">
								<span className="w-16 text-muted-foreground">{p.name}</span>
								<div className="flex-1 h-2 rounded-full bg-muted">
									<div className="h-2 rounded-full bg-primary" style={{ width: `${(perReply / maxPerReply) * 100}%` }} />
								</div>
								<span className="w-14 text-right tabular-nums">{perReplyText(perReply)}</span>
							</div>
						))}
					</div>
				)}

				<div className="mt-12 max-w-2xl mx-auto">
					<h3 className="text-lg font-semibold mb-3">What uses credits</h3>
					<dl className="grid grid-cols-[1fr_auto] gap-x-6 gap-y-2 text-sm">
						{data.actions.map((a) => (
							<div key={a.action} className="contents">
								<dt className="text-muted-foreground">{a.label}</dt>
								<dd className="text-right tabular-nums">
									{formatCredits(a.credits)} {Number(a.credits) === 1 ? "credit" : "credits"}
								</dd>
							</div>
						))}
					</dl>
					{freeCredits && <p className="mt-4 text-sm font-medium">{formatCredits(freeCredits)} free credits to start</p>}
					<p className="mt-4 text-xs text-muted-foreground">
						*Estimate if credits are used only for AI replies; other actions use credits too.
					</p>
					<p className="mt-1 text-xs text-muted-foreground">
						Payments are coming soon. For now we add credits to your project.{" "}
						<a
							href={`mailto:${site.supportEmail}`}
							className="underline underline-offset-4 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-sm"
						>
							Contact us
						</a>{" "}
						to top up.
					</p>
				</div>
			</div>
		</section>
	);
}
