"use client";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Loader2, Tag } from "lucide-react";
import ProductCatalog from "@/components/knowledge/ProductCatalog";
import { useKnowledge } from "@/components/knowledge/KnowledgeContext";

export default function ProductsSection() {
	const { projectId, selectedKb, isAdmin, imageSearchOn, reportProducts } = useKnowledge();

	return (
		<Card>
			<CardHeader>
				<CardTitle className="flex items-center gap-2"><Tag className="h-5 w-5" /> Product Catalog</CardTitle>
				<CardDescription>Products with photos, so the assistant can recognise them when customers send a picture</CardDescription>
			</CardHeader>
			<CardContent>
				{imageSearchOn === false ? (
					<p className="text-center py-4 text-muted-foreground">Image search is turned off for this project.</p>
				) : imageSearchOn && !selectedKb ? (
					<p className="text-center py-4 text-muted-foreground">No knowledge base selected</p>
				) : imageSearchOn && projectId && selectedKb ? (
					<ProductCatalog
						projectId={projectId}
						kbId={selectedKb.id}
						isAdmin={isAdmin}
						onProductsChange={reportProducts}
					/>
				) : (
					<div className="flex justify-center py-4">
						<Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
					</div>
				)}
			</CardContent>
		</Card>
	);
}
