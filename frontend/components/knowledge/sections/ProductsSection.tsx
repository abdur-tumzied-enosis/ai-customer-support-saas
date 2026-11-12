"use client";

import { useCallback, useEffect, useState } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { useToast } from "@/components/ui/toast";
import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Loader2, Plus, Tag } from "lucide-react";
import NeedsInfoBanner from "@/components/knowledge/catalog/NeedsInfoBanner";
import ProductTable, { type BulkAction } from "@/components/knowledge/catalog/ProductTable";
import ProductEditor from "@/components/knowledge/catalog/ProductEditor";
import { useKnowledge } from "@/components/knowledge/KnowledgeContext";
import ProductSourcesPanel from "@/components/knowledge/catalog/ProductSourcesPanel";
import CandidateReview from "@/components/knowledge/catalog/CandidateReview";
import { candidateApi, productApi, projectSettingsApi, type CandidatePage, type LastExtraction, type Product, type ProductStatus } from "@/lib/api";
import { formatError } from "@/lib/errors";

type Tab = "all" | ProductStatus;
const TABS: { value: Tab; label: string }[] = [
	{ value: "all", label: "All" },
	{ value: "needs_info", label: "Needs info" },
	{ value: "active", label: "Active" },
	{ value: "hidden", label: "Hidden" },
];
type View = "products" | "sources" | "review";

const POLL_MS = 3000;
// A photo that never finishes (worker down) must not keep the page polling forever
const POLL_LIMIT_MS = 5 * 60 * 1000;
const isPhotoBusy = (p: Product) => p.images.some((i) => i.status === "uploading" || i.status === "pending");

export default function ProductsSection() {
	const { projectId, selectedKb, isAdmin, imageSearchOn, reportProducts } = useKnowledge();
	const kbId = selectedKb?.id;
	const [products, setProducts] = useState<Product[] | null>(null);
	const toast = useToast();
	const [loadFailed, setLoadFailed] = useState(false);
	const [tab, setTab] = useState<Tab>("all");
	// id null = adding a product. session changes only when a different editor opens, so saving never remounts it
	const [editor, setEditor] = useState<{ id: string | null; session: number } | null>(null);
	const [stalled, setStalled] = useState(false);
	const [view, setView] = useState<View>("products");
	const [pages, setPages] = useState<CandidatePage[]>([]);
	const [lastExtraction, setLastExtraction] = useState<LastExtraction>({});
	// The project's currency, not the product's: a new product has no row to read it from
	const [currency, setCurrency] = useState("BDT");

	useEffect(() => {
		if (!projectId) return;
		projectSettingsApi.get(projectId).then((s) => setCurrency(s.currency)).catch(() => {});
	}, [projectId]);

	const load = useCallback(async () => {
		if (!projectId || !kbId) return;
		try {
			setProducts(await productApi.list(projectId, kbId));
		} catch (err) {
			toast.error(formatError(err));
			setLoadFailed(true);
		}
	}, [projectId, kbId]);

	const loadCandidates = useCallback(async () => {
		if (!projectId || !kbId) return;
		try {
			const r = await candidateApi.list(projectId, kbId);
			setPages(r.pages);
			setLastExtraction(r.last_extraction);
		} catch (err) {
			toast.error(formatError(err));
		}
	}, [projectId, kbId]);

	// Imports and syncs change both lists
	const refresh = () => { load(); loadCandidates(); };

	// Single place that tells the context about the list, whoever changed it
	useEffect(() => {
		if (products) reportProducts(products);
	}, [products, reportProducts]);

	useEffect(() => {
		setProducts(null);
		setLoadFailed(false);
		setEditor(null);
		setPages([]);
		setLastExtraction({});
		load();
		loadCandidates();
	}, [load, loadCandidates]);

	// One list, one poll: keep refreshing while any photo is still being processed
	const pending = products?.some(isPhotoBusy) ?? false;
	useEffect(() => {
		if (!pending) return;
		setStalled(false);
		const started = Date.now();
		const timer = setInterval(() => {
			if (Date.now() - started > POLL_LIMIT_MS) {
				clearInterval(timer);
				setStalled(true);
				return;
			}
			if (!document.hidden) load();
		}, POLL_MS);
		return () => clearInterval(timer);
	}, [pending, load]);

	// Adds a new product, or replaces the one that changed
	const put = (saved: Product) =>
		setProducts((prev) => {
			const list = prev ?? [];
			return list.some((p) => p.id === saved.id) ? list.map((p) => (p.id === saved.id ? saved : p)) : [...list, saved];
		});

	// One request per product; a failed one is reported, the others still apply
	const runBulk = async (action: BulkAction, ids: string[]) => {
		const results = await Promise.allSettled(ids.map(async (id) => {
			if (action === "delete") {
				await productApi.remove(id);
				return null;
			}
			return productApi.patch(id, { status: action });
		}));
		const updated = results.flatMap((r) => (r.status === "fulfilled" && r.value ? [r.value] : []));
		const removed = new Set(ids.filter((_, i) => action === "delete" && results[i].status === "fulfilled"));
		setProducts((prev) => (prev ?? []).filter((p) => !removed.has(p.id)).map((p) => updated.find((u) => u.id === p.id) ?? p));
		const failed = results.filter((r) => r.status === "rejected").length;
		if (failed) toast.error(`${failed} of ${ids.length} could not be updated. A product needs a price to be set active.`);
	};

	const openEditor = (id: string | null) => setEditor((e) => ({ id, session: (e?.session ?? 0) + 1 }));

	const reviewCount = pages.reduce((n, p) => n + p.candidates.length, 0);
	const needsInfo = products?.filter((p) => p.status === "needs_info").length ?? 0;
	// The list endpoint has no status filter, so the tabs filter here
	const shown = products?.filter((p) => tab === "all" || p.status === tab) ?? [];

	return (
		<Card>
			<CardHeader className="flex flex-row items-start justify-between gap-4 space-y-0">
				<div className="space-y-1.5">
					<CardTitle className="flex items-center gap-2"><Tag className="h-5 w-5" /> Products</CardTitle>
					<CardDescription>Prices, details and photos the assistant uses to answer customers and take orders</CardDescription>
				</div>
				{isAdmin && selectedKb && (
					<Button onClick={() => { setView("products"); openEditor(null); }}><Plus className="mr-2 h-4 w-4" />Add product</Button>
				)}
			</CardHeader>
			<CardContent className="space-y-4">
				{!selectedKb ? (
					<p className="text-center py-4 text-muted-foreground">No agent memory selected</p>
				) : products === null && loadFailed ? (
					<div className="flex justify-center py-4">
						<Button variant="outline" onClick={() => { setLoadFailed(false); load(); }}>Retry</Button>
					</div>
				) : products === null ? (
					<div className="flex justify-center py-4"><Loader2 className="h-5 w-5 animate-spin text-muted-foreground" /></div>
				) : (
					<>
						<Tabs value={view} onValueChange={(v) => setView(v as View)}>
							<TabsList>
								<TabsTrigger value="products">Product list</TabsTrigger>
								{isAdmin && <TabsTrigger value="sources">Sources</TabsTrigger>}
								<TabsTrigger value="review">Review ({reviewCount})</TabsTrigger>
							</TabsList>
						</Tabs>
						{view === "sources" && isAdmin && projectId && kbId && (
							<ProductSourcesPanel projectId={projectId} kbId={kbId} onChanged={refresh} />
						)}
						{view === "review" && projectId && kbId && (
							<CandidateReview
								projectId={projectId}
								kbId={kbId}
								pages={pages}
								lastExtraction={lastExtraction}
								currency={currency}
								isAdmin={isAdmin}
								onChanged={refresh}
							/>
						)}
						{view === "products" && (<>
						<NeedsInfoBanner count={needsInfo} />
						{stalled && pending && (
							<Alert><AlertDescription>Still indexing. This is taking longer than usual. Reload the page to check again.</AlertDescription></Alert>
						)}
						<Tabs value={tab} onValueChange={(v) => setTab(v as Tab)}>
							<TabsList>
								{TABS.map((t) => <TabsTrigger key={t.value} value={t.value}>{t.label}</TabsTrigger>)}
							</TabsList>
						</Tabs>
						{editor && projectId && kbId && (
							<ProductEditor
								key={editor.session}
								projectId={projectId}
								kbId={kbId}
								product={products.find((p) => p.id === editor.id) ?? null}
								currency={currency}
								imageSearchOff={imageSearchOn === false}
								onSaved={(p) => { put(p); setEditor((e) => e && { ...e, id: p.id }); }}
								onDeleted={(id) => { setProducts((prev) => (prev ?? []).filter((p) => p.id !== id)); setEditor(null); }}
								onClose={() => setEditor(null)}
							/>
						)}
						<ProductTable
							products={shown}
							isAdmin={isAdmin}
							onBulk={runBulk}
							onEdit={(p) => openEditor(p.id)}
							onSavePrice={async (p, price) => put(await productApi.patch(p.id, { price }))}
						/>
						</>)}
					</>
				)}
			</CardContent>
		</Card>
	);
}
