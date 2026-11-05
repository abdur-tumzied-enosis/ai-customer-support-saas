"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Loader2, Plus, Trash2 } from "lucide-react";
import { productFieldApi, productApi, type Product, type ProductField, type ProductVariant } from "@/lib/api";
import { formatError } from "@/lib/errors";
import { currencySymbol } from "@/components/knowledge/catalog/ProductTable";

export const KEY_WARNING =
	"Add your product ID or SKU so updates match the right product. Without it we match by name, which can mix up similar products.";

type Props = {
	projectId: string;
	kbId: string;
	product: Product;
	onSaved: (p: Product) => void;
	onClose: () => void;
};

const blankVariant = (): ProductVariant => ({ external_id: null, sku: "", label: "", attributes: {}, price: null, available: true });

export default function ProductEditor({ projectId, kbId, product, onSaved, onClose }: Props) {
	const [name, setName] = useState(product.name);
	const [description, setDescription] = useState(product.description);
	const [sku, setSku] = useState(product.sku);
	const [externalId, setExternalId] = useState(product.external_id ?? "");
	const [price, setPrice] = useState(product.price ?? "");
	const [available, setAvailable] = useState(product.available);
	const [hidden, setHidden] = useState(product.status === "hidden");
	const [custom, setCustom] = useState<Record<string, unknown>>(product.custom);
	const [variants, setVariants] = useState<ProductVariant[]>(product.variants);
	const [fields, setFields] = useState<ProductField[]>([]);
	const [saving, setSaving] = useState(false);
	const [error, setError] = useState<string | null>(null);

	useEffect(() => {
		productFieldApi.list(projectId, kbId).then(setFields).catch(() => {});
	}, [projectId, kbId]);

	const setVariant = (i: number, patch: Partial<ProductVariant>) =>
		setVariants((vs) => vs.map((v, j) => (j === i ? { ...v, ...patch } : v)));

	const save = async () => {
		setSaving(true);
		setError(null);
		try {
			const saved = await productApi.patch(product.id, {
				name, description, sku, external_id: externalId, price, available, custom,
				// un-hiding goes through needs_info; the backend then decides active vs needs info
				status: hidden ? "hidden" : product.status === "hidden" ? "needs_info" : undefined,
				variants: variants.map((v) => ({
					external_id: v.external_id, sku: v.sku, label: v.label, attributes: v.attributes,
					price: v.price === "" ? null : v.price, available: v.available,
				})),
			});
			onSaved(saved);
			onClose();
		} catch (err) {
			setError(formatError(err));
		} finally {
			setSaving(false);
		}
	};

	return (
		<div className="space-y-4 rounded-lg border p-4">
			{!sku.trim() && !externalId.trim() && (
				<Alert><AlertDescription>{KEY_WARNING}</AlertDescription></Alert>
			)}
			{error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
			<div className="grid gap-3 sm:grid-cols-2">
				<div className="space-y-1"><Label htmlFor="pe-name">Name</Label><Input id="pe-name" value={name} onChange={(e) => setName(e.target.value)} /></div>
				<div className="space-y-1">
					<Label htmlFor="pe-price">Price ({currencySymbol(product.currency)})</Label>
					<Input id="pe-price" inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} />
				</div>
				<div className="space-y-1"><Label htmlFor="pe-sku">SKU</Label><Input id="pe-sku" value={sku} onChange={(e) => setSku(e.target.value)} /></div>
				<div className="space-y-1"><Label htmlFor="pe-ext">Product ID</Label><Input id="pe-ext" value={externalId} onChange={(e) => setExternalId(e.target.value)} /></div>
			</div>
			<div className="space-y-1"><Label htmlFor="pe-desc">Description</Label><Textarea id="pe-desc" value={description} onChange={(e) => setDescription(e.target.value)} /></div>
			<div className="flex gap-4 text-sm">
				<label className="flex items-center gap-2"><input type="checkbox" checked={available} onChange={(e) => setAvailable(e.target.checked)} /> In stock</label>
				<label className="flex items-center gap-2"><input type="checkbox" checked={hidden} onChange={(e) => setHidden(e.target.checked)} /> Hidden from the assistant</label>
			</div>

			{fields.length > 0 && (
				<div className="grid gap-3 sm:grid-cols-2">
					{fields.map((f) => (
						<div key={f.id} className="space-y-1">
							<Label htmlFor={`pe-f-${f.key}`}>{f.label}{f.required && " *"}</Label>
							<Input
								id={`pe-f-${f.key}`}
								value={String(custom[f.key] ?? "")}
								onChange={(e) => setCustom((c) => ({ ...c, [f.key]: e.target.value }))}
							/>
						</div>
					))}
				</div>
			)}

			<div className="space-y-2">
				<div className="flex items-center justify-between">
					<Label>Variants</Label>
					<Button type="button" variant="outline" size="sm" onClick={() => setVariants((vs) => [...vs, blankVariant()])}>
						<Plus className="h-4 w-4" /> Add variant
					</Button>
				</div>
				{variants.map((v, i) => (
					<div key={v.id ?? i} className="flex flex-wrap items-center gap-2">
						<Input className="w-40" placeholder="Label" aria-label="Variant label" value={v.label} onChange={(e) => setVariant(i, { label: e.target.value })} />
						<Input className="w-32" placeholder="SKU" aria-label="Variant SKU" value={v.sku} onChange={(e) => setVariant(i, { sku: e.target.value })} />
						<Input className="w-28" placeholder="Price" aria-label="Variant price" inputMode="decimal" value={v.price ?? ""} onChange={(e) => setVariant(i, { price: e.target.value })} />
						<label className="flex items-center gap-1 text-sm"><input type="checkbox" checked={v.available} onChange={(e) => setVariant(i, { available: e.target.checked })} /> In stock</label>
						<Button type="button" variant="ghost" size="icon" aria-label="Remove variant" onClick={() => setVariants((vs) => vs.filter((_, j) => j !== i))}>
							<Trash2 className="h-4 w-4" />
						</Button>
					</div>
				))}
			</div>

			<div className="flex gap-2">
				<Button onClick={save} disabled={saving || !name.trim()}>{saving && <Loader2 className="h-4 w-4 animate-spin" />} Save</Button>
				<Button variant="outline" onClick={onClose} disabled={saving}>Cancel</Button>
			</div>
		</div>
	);
}
