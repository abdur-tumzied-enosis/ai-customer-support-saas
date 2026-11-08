"use client";

import { useState } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { useToast } from "@/components/ui/toast";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Loader2 } from "lucide-react";
import { candidateApi, type CandidatePage } from "@/lib/api";
import { formatError } from "@/lib/errors";
import { currencySymbol } from "./ProductTable";

const LABELS: Record<string, string> = { description: "Description", sku: "SKU", external_id: "Product ID", price: "Price" };

const fmt = (field: string, v: string | null, currency: string) =>
	v ? (field === "price" ? `${currencySymbol(currency)}${v}` : v) : "empty";

type Props = {
	projectId: string;
	kbId: string;
	pages: CandidatePage[];
	currency: string;
	isAdmin: boolean;
	onChanged: () => void;
};

export default function CandidateReview({ projectId, kbId, pages, currency, isAdmin, onChanged }: Props) {
	const [picked, setPicked] = useState<Set<string>>(new Set());
	const [index, setIndex] = useState(0);
	const [busy, setBusy] = useState(false);
	const toast = useToast();
	const [rejected, setRejected] = useState<{ id: string; message: string }[]>([]);

	const names = new Map(pages.flatMap((p) => p.candidates.map((c) => [c.id, c.name] as const)));

	const toggle = (ids: string[], on: boolean) =>
		setPicked((prev) => {
			const next = new Set(prev);
			ids.forEach((id) => (on ? next.add(id) : next.delete(id)));
			return next;
		});

	const run = async (fn: () => Promise<void>) => {
		setBusy(true);
		try {
			await fn();
		} catch (err) {
			toast.error(formatError(err));
		} finally {
			setBusy(false);
		}
	};

	const current = Math.min(index, pages.length - 1);
	const page = pages[current];

	const importPicked = () => run(async () => {
		// A page with every product picked disappears after the reload, so the next page slides into this slot.
		const pageGoesAway = page.candidates.every((c) => picked.has(c.id));
		const r = await candidateApi.import(projectId, kbId, [...picked]);
		setRejected(r.rejected);
		toast.success(`${r.imported} added, ${r.updated} updated`);
		setPicked(new Set());
		if (!pageGoesAway) setIndex(current + 1);
		onChanged();
	});

	const ignore = (url: string) => run(async () => {
		await candidateApi.ignorePage(projectId, kbId, url);
		onChanged();
	});

	const rerun = () => run(async () => {
		await candidateApi.extract(projectId, kbId);
		toast.success("Extraction started. Check back in a minute.");
	});

	return (
		<div className="space-y-4">
			<div className="flex items-center justify-between gap-2">
				<p className="text-sm text-muted-foreground">Nothing is added to your catalog until you pick it.</p>
				{isAdmin && <Button variant="outline" size="sm" disabled={busy} onClick={rerun}>Re-run extraction</Button>}
			</div>
			{rejected.length > 0 && (
				<Alert variant="destructive">
					<AlertDescription>
						<ul className="list-disc pl-5">{rejected.map((r) => <li key={r.id}>{names.get(r.id) ?? r.id}: {r.message}</li>)}</ul>
					</AlertDescription>
				</Alert>
			)}
			{pages.length === 0 && <p className="py-4 text-center text-muted-foreground">No products waiting for review</p>}
			{page && pages.length > 1 && (
				<div className="flex items-center justify-between text-sm">
					<span className="text-muted-foreground">Page {current + 1} of {pages.length}</span>
					<span className="flex gap-2">
						<Button size="sm" variant="outline" disabled={current === 0} onClick={() => setIndex(current - 1)}>Previous page</Button>
						<Button size="sm" variant="outline" disabled={current >= pages.length - 1} onClick={() => setIndex(current + 1)}>Next page</Button>
					</span>
				</div>
			)}
			{page && (() => {
				const ids = page.candidates.map((c) => c.id);
				return (
					<div key={page.page_url} className="space-y-2 rounded-md border p-3">
						<div className="flex items-center justify-between gap-2">
							<div className="min-w-0">
								<p className="truncate font-medium">{page.page_title || page.page_url}</p>
								<p className="truncate text-xs text-muted-foreground">{page.page_url}</p>
							</div>
							{isAdmin && (
								<span className="flex shrink-0 gap-2">
									<Button size="sm" variant="outline" onClick={() => toggle(ids, true)}>Select all from this page</Button>
									<Button size="sm" variant="outline" disabled={busy} onClick={() => ignore(page.page_url)}>Ignore this page next time</Button>
								</span>
							)}
						</div>
						{page.candidates.map((c) => (
							<label
								key={c.id}
								className={`flex gap-3 rounded-md p-2 text-sm ${c.low_confidence ? "bg-amber-50 dark:bg-amber-950/30" : ""}`}
							>
								{isAdmin && (
									<input type="checkbox" checked={picked.has(c.id)} onChange={(e) => toggle([c.id], e.target.checked)} />
								)}
								<span className="flex shrink-0 gap-1">
									{(c.payload.image_srcs ?? []).slice(0, 3).map((src) => (
										// eslint-disable-next-line @next/next/no-img-element
										<img key={src} src={src} alt="" className="h-12 w-12 rounded object-cover" />
									))}
								</span>
								<span className="min-w-0 space-y-1">
									<span className="flex flex-wrap items-center gap-2">
										<span className="font-medium">{c.name}</span>
										{c.matched_product_id ? <Badge variant="secondary">Update</Badge> : <Badge variant="outline">New</Badge>}
										{c.matched_by === "name" && <Badge variant="outline">Matched by name</Badge>}
										{c.low_confidence && <Badge variant="outline">Check this one</Badge>}
									</span>
									{c.matched_product_id ? (
										Object.entries(c.changes).map(([f, ch]) => (
											<span key={f} className="block text-muted-foreground">
												{LABELS[f] ?? f}: {fmt(f, ch.old, currency)} → {fmt(f, ch.new, currency)}
											</span>
										))
									) : (
										<span className="block text-muted-foreground">
											{[c.payload.price && fmt("price", c.payload.price, currency), c.payload.sku && `SKU ${c.payload.sku}`].filter(Boolean).join(" · ")}
										</span>
									)}
								</span>
							</label>
						))}
					</div>
				);
			})()}
			{isAdmin && picked.size > 0 && (
				<Button disabled={busy} onClick={importPicked}>
					{busy && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}Add {picked.size} selected{pages.length > 1 ? " and go to next page" : ""}
				</Button>
			)}
		</div>
	);
}
