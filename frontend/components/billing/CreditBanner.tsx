"use client";

import { Alert } from "@/components/ui/alert";
import { formatCredits, type Wallet } from "@/lib/billing-api";

export default function CreditBanner({ wallet }: { wallet: Wallet | null }) {
	if (!wallet || (!wallet.empty && !wallet.low)) return null;
	return (
		<div className="max-w-6xl mx-auto px-6 pt-4">
			{wallet.empty ? (
				<Alert variant="destructive">
					Your project is out of credits. Shoppers are being handed to your team instead of the AI. Contact us to top up.
				</Alert>
			) : (
				<Alert>
					Your project has {formatCredits(wallet.balance)} credits left. Contact us to top up before the AI stops replying.
				</Alert>
			)}
		</div>
	);
}
