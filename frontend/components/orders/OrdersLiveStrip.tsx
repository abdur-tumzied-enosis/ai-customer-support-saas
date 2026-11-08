"use client";

import Link from "next/link";
import type { Route } from "next";
import { Button } from "@/components/ui/button";

const CHANNEL_NAMES: Record<string, string> = {
	facebook: "Facebook",
	instagram: "Instagram",
	whatsapp: "WhatsApp",
	widget: "your website chat",
};

function joinNames(names: string[]): string {
	if (names.length <= 1) return names.join("");
	return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

type Props = { channels: string[]; isAdmin: boolean; onOpenTestChat: () => void };

// Shown while ordering is on: where orders come from, and a way to try it as a customer would
export default function OrdersLiveStrip({ channels, isAdmin, onOpenTestChat }: Props) {
	const names = channels.filter((c) => c !== "self").map((c) => CHANNEL_NAMES[c] ?? c);
	const where = names.length
		? `Taking orders on ${joinNames(names)}.`
		: "Orders are on, but no channel is picked yet.";

	return (
		<div
			role="status"
			className="flex flex-col gap-3 rounded-lg border bg-card px-4 py-3 sm:flex-row sm:items-center sm:justify-between"
		>
			<div className="flex items-start gap-3">
				<span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-green-600" aria-hidden />
				<p className="text-sm">
					{where} <span className="text-muted-foreground">Your test chat takes orders too, so you can try it first.</span>
					{!names.length && isAdmin && (
						<>
							{" "}
							<Link href={"/orders/setup" as Route} className="text-primary underline">
								Pick channels
							</Link>
						</>
					)}
				</p>
			</div>
			<Button variant="outline" size="sm" className="shrink-0 self-start sm:self-auto" onClick={onOpenTestChat}>
				Open test chat
			</Button>
		</div>
	);
}
