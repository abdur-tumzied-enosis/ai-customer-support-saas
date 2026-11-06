"use client";

import { useCallback, useEffect, useState } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Loader2, Tag } from "lucide-react";
import ProductCatalog from "@/components/knowledge/ProductCatalog";
import NeedsInfoBanner from "@/components/knowledge/catalog/NeedsInfoBanner";
import ProductTable from "@/components/knowledge/catalog/ProductTable";
import ProductEditor from "@/components/knowledge/catalog/ProductEditor";
import { useKnowledge } from "@/components/knowledge/KnowledgeContext";
import ProductSourcesPanel from "@/components/knowledge/catalog/ProductSourcesPanel";
import CandidateReview from "@/components/knowledge/catalog/CandidateReview";
import { candidateApi, productApi, type CandidatePage, type Product, type ProductStatus } from "@/lib/api";
import { formatError } from "@/lib/errors";

type Tab = "all" | ProductStatus;
const TABS: { value: Tab; label: string }[] = [
	{ value: "all", label: "All" },
	{ value: "needs_info", label: "Needs info" },
	{ value: "active", label: "Active" },
	{ value: "hidden", label: "Hidden" },
];
type View = "products" | "sources" | "review";

export default function ProductsSection() {
	const { projectId, selectedKb, isAdmin, imageSearchOn, reportProducts } = useKnowledge();
	const kbId = selectedKb?.id;
	const [products, setProducts] = useState<Product[] | null>(null);
	const [error, setError] = useState<string | null>(null);
	const [loadFailed, setLoadFailed] = useState(false);
	const [tab, setTab] = useState<Tab>("all");
	const [editing, setEditing] = useState<Product | null>(null);
	const [view, setView] = useState<View>("products");
	const [pages, setPages] = useState<CandidatePage[]>([]);

	const load = useCallback(async () => {
		if (!projectId || !kbId) return;
		setError(null);
		try {
			setProducts(await productApi.list(projectId, kbId));
		} catch (err) {
			setError(formatError(err));
			setLoadFailed(true);
		}
	}, [projectId, kbId]);

	const loadCandidates = useCallback(async () => {
		if (!projectId || !kbId) return;
		try {
			setPages((await candidateApi.list(projectId, kbId)).pages);
		} catch (err) {
			setError(formatError(err));
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
		setEditing(null);
		setPages([]);
		load();
		loadCandidates();
	}, [load, loadCandidates]);

	const replace = (saved: Product) => {
		setProducts((prev) => (prev ?? []).map((p) => (p.id === saved.id ? saved : p)));
	};

	const reviewCount = pages.reduce((n, p) => n + p.candidates.length, 0);
	const needsInfo = products?.filter((p) => p.status === "needs_info").length ?? 0;
	// The list endpoint has no status filter, so the tabs filter here
	const shown = products?.filter((p) => tab === "all" || p.status === tab) ?? [];

	return (
		<div className="space-y-6">
			<Card>
				<CardHeader>
					<CardTitle className="flex items-center gap-2"><Tag className="h-5 w-5" /> Product Catalog</CardTitle>
					<CardDescription>Prices and details the assistant uses to answer customers and take orders</CardDescription>
				</CardHeader>
				<CardContent className="space-y-4">
					{error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
					{!selectedKb ? (
						<p className="text-center py-4 text-muted-foreground">No knowledge base selected</p>
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
									<TabsTrigger value="products">Products</TabsTrigger>
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
									currency={products[0]?.currency ?? "BDT"}
									isAdmin={isAdmin}
									onChanged={refresh}
								/>
							)}
							{view === "products" && (<>
							<NeedsInfoBanner count={needsInfo} />
							<Tabs value={tab} onValueChange={(v) => setTab(v as Tab)}>
								<TabsList>
									{TABS.map((t) => <TabsTrigger key={t.value} value={t.value}>{t.label}</TabsTrigger>)}
								</TabsList>
							</Tabs>
							{editing && projectId && (
								<ProductEditor
									key={editing.id}
									projectId={projectId}
									kbId={editing.kb_id}
									product={editing}
									onSaved={replace}
									onClose={() => setEditing(null)}
								/>
							)}
							<ProductTable
								products={shown}
								onEdit={setEditing}
								onSavePrice={async (p, price) => replace(await productApi.patch(p.id, { price }))}
							/>
							</>)}
						</>
					)}
				</CardContent>
			</Card>

			{/* Photos need image search; the catalog above does not */}
			{imageSearchOn && projectId && selectedKb && (
				<Card>
					<CardHeader>
						<CardTitle>Product photos</CardTitle>
						<CardDescription>Photos let the assistant recognise a product when a customer sends a picture</CardDescription>
					</CardHeader>
					<CardContent>
						<ProductCatalog projectId={projectId} kbId={selectedKb.id} isAdmin={isAdmin} onProductsChange={setProducts} />
					</CardContent>
				</Card>
			)}
		</div>
	);
}
