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
	const freeCredits = Number(data.signup_credits) > 0 ? data.signup_credits : null;

	return (
		<section id="pricing" className="py-20 px-6 scroll-mt-4">
			<div className="container mx-auto max-w-6xl">
				<h2 className="text-3xl font-bold text-center">Pay for the replies you send</h2>
				<p className="text-muted-foreground text-center mt-3 mb-12">
					Buy credits once and use them on every channel. Bigger packs cost less per reply.
				</p>

				{freeCredits && (
					<div className="mb-10 mx-auto max-w-3xl rounded-xl border border-[hsl(var(--marigold))] bg-[hsl(var(--marigold)/0.12)] p-5 text-center">
						<p className="text-lg font-semibold">Start with {formatCredits(freeCredits)} free credits</p>
						<p className="text-sm text-muted-foreground mt-1">
							{replyCredits > 0 ? `About ${formatCredits(Math.floor(Number(freeCredits) / replyCredits))} AI replies. ` : ""}
							No card needed. Buy a pack only when you run out.
						</p>
					</div>
				)}

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
									<p className={cn("text-sm font-medium", p.highlighted ? "text-primary-foreground" : "text-emerald-700 dark:text-emerald-400")}>
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

				<div className="mt-14 max-w-2xl mx-auto">
					<h3 className="text-lg font-semibold">What uses credits</h3>
					<p className="text-sm text-muted-foreground mt-1">
						Your credits are shared across every channel. Most of them go on replies; setup helpers cost a fraction or a few credits.
					</p>
					<dl className="mt-4 rounded-xl border border-border bg-card divide-y divide-border text-sm">
						{data.actions.map((a) => (
							<div key={a.action} className="flex items-center justify-between gap-4 px-4 py-3">
								<dt>{a.label}</dt>
								<dd className="tabular-nums font-medium whitespace-nowrap">
									{formatCredits(a.credits)} {Number(a.credits) === 1 ? "credit" : "credits"}
								</dd>
							</div>
						))}
					</dl>
					<p className="mt-4 text-xs text-muted-foreground">
						*Estimate if credits are used only for AI replies; other actions use credits too.
					</p>
					<p className="mt-2 text-sm text-muted-foreground">
						Online payment is coming soon. To top up now, email{" "}
						<a
							href={`mailto:${site.supportEmail}`}
							className="underline underline-offset-4 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-sm"
						>
							{site.supportEmail}
						</a>{" "}
						and we will add credits to your project.
					</p>
				</div>
			</div>
		</section>
	);
}
