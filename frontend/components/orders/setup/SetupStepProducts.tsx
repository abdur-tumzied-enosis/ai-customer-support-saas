"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import CandidateReview from "@/components/knowledge/catalog/CandidateReview";
import { useToast } from "@/components/ui/toast";
import ProductTable from "@/components/knowledge/catalog/ProductTable";
import { ORDERS_CHANGED } from "@/components/orders/OrdersShell";
import { candidateApi, productApi, type CandidatePage, type LastExtraction, type Product } from "@/lib/api";
import { orderApi, type Requirement } from "@/lib/orders-api";
import { formatError } from "@/lib/errors";

type Props = { projectId: string; kbId: string; onBack: () => void };

// Why turning orders on is blocked, in words the user can act on.
const BLOCK_REASON: Record<string, string> = {
	channel: "Connect a channel first. Open Channels.",
	product_source: "Add product information first. Open Knowledge.",
};

// Step 2. Zone A adds products from the website; zone B is your products, with the rows that need a price first.
export default function SetupStepProducts({ projectId, kbId, onBack }: Props) {
	const router = useRouter();
	const [pages, setPages] = useState<CandidatePage[] | null>(null);
	const [lastExtraction, setLastExtraction] = useState<LastExtraction>({});
	const [products, setProducts] = useState<Product[]>([]);
	const [counts, setCounts] = useState<Record<string, number>>({});
	const [currency, setCurrency] = useState("BDT");
	const [requirements, setRequirements] = useState<Requirement[]>([]);
	const [error, setError] = useState<string | null>(null);
	const toast = useToast();
	const [busy, setBusy] = useState(false);
	const tableRef = useRef<HTMLDivElement>(null);

	const load = useCallback(async () => {
		try {
			const [candidates, summary, list, reqs] = await Promise.all([
				candidateApi.list(projectId, kbId),
				productApi.summary(projectId, kbId),
				productApi.list(projectId, kbId),
				orderApi.requirements(projectId),
			]);
			setPages(candidates.pages);
			setLastExtraction(candidates.last_extraction);
			setCounts(summary.counts);
			setCurrency(summary.currency);
			setProducts(list);
			setRequirements(reqs.data);
		} catch (e) {
			setError(formatError(e));
			toast.error(formatError(e));
		}
	}, [projectId, kbId]);

	useEffect(() => {
		load();
	}, [load]);

	// Rows that need a price go to the top; the rest keep their order.
	const sorted = useMemo(
		() => [...products].sort((a, b) => Number(b.status === "needs_info") - Number(a.status === "needs_info")),
		[products],
	);

	if (!pages) {
		return error ? (
			<Alert variant="destructive">
				<AlertDescription>{error}</AlertDescription>
			</Alert>
		) : (
			<Loader2 className="h-5 w-5 animate-spin" />
		);
	}

	const active = counts.active ?? 0;
	const needPrice = counts.needs_info ?? 0;
	const blocker = requirements.find((r) => r.hard && r.status === "fail");
	const reason =
		active === 0
			? "Add a price to at least one product first."
			: blocker
				? (BLOCK_REASON[blocker.key] ?? blocker.label)
				: null;

	function jumpToFirstPrice() {
		tableRef.current?.querySelector<HTMLInputElement>('input[data-needs-price="true"]')?.focus();
	}

	async function turnOn() {
		setBusy(true);
		try {
			await orderApi.enable(projectId);
			window.dispatchEvent(new Event(ORDERS_CHANGED));
			router.push("/orders");
		} catch (e) {
			toast.error(formatError(e));
			setBusy(false);
		}
	}

	return (
		<div className="space-y-8 pb-28">

			<section className="space-y-3">
				<h2 className="text-base font-medium">From your website</h2>
				<CandidateReview projectId={projectId} kbId={kbId} pages={pages} lastExtraction={lastExtraction} currency={currency} isAdmin onChanged={load} />
			</section>

			{products.length > 0 && (
				<section className="space-y-3">
					<h2 className="text-base font-medium">Your products</h2>
					<p className="text-sm text-muted-foreground">
						{active} products ready to sell. {needPrice} need a price.
						{needPrice > 0 && (
							<>
								{" "}
								<Button variant="link" className="h-auto p-0 align-baseline" onClick={jumpToFirstPrice}>
									Add a price
								</Button>
							</>
						)}
					</p>
					<div ref={tableRef} className="overflow-x-auto">
						<ProductTable
							products={sorted}
							isAdmin={false}
							compact
							saveOnBlur
							onEdit={() => undefined}
							onSavePrice={async (p, price) => {
								await productApi.patch(p.id, { price });
								await load();
							}}
						/>
					</div>
				</section>
			)}

			<div className="sticky bottom-0 -mx-4 border-t bg-background/95 px-4 py-3 backdrop-blur sm:-mx-1 sm:px-1">
				<div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
					<Button variant="outline" onClick={onBack}>
						Back
					</Button>
					<div className="flex flex-col gap-1 sm:items-end">
						{needPrice > 0 && active > 0 && (
							<p className="text-sm text-amber-700 dark:text-amber-400">
								{needPrice} products without a price stay hidden until you add one in Knowledge &gt; Products.
							</p>
						)}
						{reason && (
							<p id="turn-on-reason" className="text-sm text-muted-foreground">
								{reason}
							</p>
						)}
						<Button onClick={turnOn} disabled={busy || reason !== null} aria-describedby={reason ? "turn-on-reason" : undefined}>
							{busy ? "Turning on…" : "Turn on orders"}
						</Button>
					</div>
				</div>
			</div>
		</div>
	);
}
