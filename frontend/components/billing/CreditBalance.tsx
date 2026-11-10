"use client";

import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { formatCredits, type Wallet } from "@/lib/billing-api";

export default function CreditBalance({ wallet, isAdmin }: { wallet: Wallet | null; isAdmin: boolean }) {
	if (!wallet) return null;
	const badge = (
		<Badge variant={wallet.empty ? "destructive" : "outline"} className="whitespace-nowrap">
			{formatCredits(wallet.balance)} credits
		</Badge>
	);
	return isAdmin ? (
		<Link href="/credits" aria-label="View credit history">
			{badge}
		</Link>
	) : (
		badge
	);
}
