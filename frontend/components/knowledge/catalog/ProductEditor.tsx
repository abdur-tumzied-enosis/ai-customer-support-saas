"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { useToast } from "@/components/ui/toast";
import { Badge } from "@/components/ui/badge";
import { Loader2, Plus, Trash2, Upload, X } from "lucide-react";
import { API_BASE_URL, productApi, productFieldApi, type Product, type ProductField, type ProductImage, type ProductVariant } from "@/lib/api";
import { formatError } from "@/lib/errors";
import { currencySymbol } from "@/components/knowledge/catalog/ProductTable";

export const KEY_WARNING =
	"Add your product ID or SKU so updates match the right product. Without it we match by name, which can mix up similar products.";

const IMAGE_ACCEPT = "image/jpeg,image/png,image/webp";
const PHOTO_BADGE: Record<string, string> = { uploading: "Uploading", pending: "Indexing", failed: "Failed" };
const HALF_SAVED = "Product added, but price and SKU didn't save. Check them and select Save changes.";

// Where a product came from; the editor says so
const ORIGIN: Record<string, string> = {
	kb: "an agent memory page",
	csv: "a CSV or Excel file",
	sheet: "a Google Sheet",
	api: "a REST API",
};

type Props = {
	projectId: string;
	kbId: string;
	/** null = add a product */
	product: Product | null;
	currency: string;
	imageSearchOff: boolean;
	/** Every product the server returned: a new product, or the changed one */
	onSaved: (p: Product) => void;
	onDeleted: (id: string) => void;
	onClose: () => void;
};

const blankVariant = (): ProductVariant => ({ external_id: null, sku: "", label: "", attributes: {}, price: null, available: true });

export default function ProductEditor({ projectId, kbId, product, currency, imageSearchOff, onSaved, onDeleted, onClose }: Props) {
	// After a product is created, this editor keeps saving to it instead of creating another
	const [savedId, setSavedId] = useState<string | null>(product?.id ?? null);
	const [createdHere, setCreatedHere] = useState(false);
	const [initialStatus] = useState(product?.status);
	const [name, setName] = useState(product?.name ?? "");
	const [description, setDescription] = useState(product?.description ?? "");
	const [sku, setSku] = useState(product?.sku ?? "");
	const [externalId, setExternalId] = useState(product?.external_id ?? "");
	const [price, setPrice] = useState(product?.price ?? "");
	const [available, setAvailable] = useState(product?.available ?? true);
	const [hidden, setHidden] = useState(product?.status === "hidden");
	const [custom, setCustom] = useState<Record<string, unknown>>(product?.custom ?? {});
	const [variants, setVariants] = useState<ProductVariant[]>(product?.variants ?? []);
	const [fields, setFields] = useState<ProductField[]>([]);
	const [files, setFiles] = useState<File[]>([]); // add mode: photos upload with the product
	const previews = useMemo(() => files.map((f) => URL.createObjectURL(f)), [files]);
	useEffect(() => () => previews.forEach((u) => URL.revokeObjectURL(u)), [previews]);
	const [saving, setSaving] = useState(false);
	const toast = useToast();

	useEffect(() => {
		productFieldApi.list(projectId, kbId).then(setFields).catch(() => {});
	}, [projectId, kbId]);

	const setVariant = (i: number, patch: Partial<ProductVariant>) =>
		setVariants((vs) => vs.map((v, j) => (j === i ? { ...v, ...patch } : v)));

	const hasDetails = () =>
		Boolean(price.trim() || sku.trim() || externalId.trim() || variants.length || hidden || !available ||
			Object.values(custom).some((v) => String(v ?? "").trim()));

	const details = () => ({
		name: name.trim(), description: description.trim(), sku, external_id: externalId, price, available, custom,
		// un-hiding goes through needs_info; the backend then decides active vs needs info
		status: hidden ? ("hidden" as const) : initialStatus === "hidden" ? ("needs_info" as const) : undefined,
		variants: variants.map((v) => ({
			external_id: v.external_id, sku: v.sku, label: v.label, attributes: v.attributes,
			price: v.price === "" ? null : v.price, available: v.available,
		})),
	});

	const save = async () => {
		setSaving(true);
		let id = savedId;
		let created = false;
		try {
			if (id === null) {
				// Name, description and photos only: the backend cannot take the rest on create
				const made = await productApi.create(projectId, kbId, name.trim(), description.trim(), files);
				id = made.id;
				created = true;
				setSavedId(id);
				setCreatedHere(true);
				setFiles([]);
				onSaved(made);
				if (!hasDetails()) return onClose();
			}
			onSaved(await productApi.patch(id, details()));
			onClose();
		} catch (err) {
			toast.error(created || createdHere ? HALF_SAVED : formatError(err));
		} finally {
			setSaving(false);
		}
	};

	const photoOp = async (fn: () => Promise<void>) => {
		try {
			await fn();
		} catch (err) {
			toast.error(formatError(err));
		}
	};

	const addPhotos = (list: FileList | null) => {
		if (!list?.length) return;
		if (savedId === null) return setFiles((f) => [...f, ...Array.from(list)]);
		photoOp(async () => onSaved(await productApi.addImages(savedId, Array.from(list))));
	};

	const removePhoto = (img: ProductImage) =>
		product && photoOp(async () => {
			await productApi.removeImage(img.id);
			onSaved({ ...product, images: product.images.filter((i) => i.id !== img.id) });
		});

	const remove = async () => {
		if (!product) return;
		const n = product.images.length;
		const photos = n ? ` and its ${n} photo${n === 1 ? "" : "s"}` : "";
		if (!window.confirm(`Delete ${product.name}${photos}?`)) return;
		setSaving(true);
		try {
			await productApi.remove(product.id);
			onDeleted(product.id);
		} catch (err) {
			toast.error(formatError(err));
			setSaving(false);
		}
	};

	const images = product?.images ?? [];

	return (
		<div className="space-y-4 rounded-lg border p-4">
			{product && product.source in ORIGIN && (
				<p className="text-xs text-muted-foreground">
					From {ORIGIN[product.source]}
					{product.source === "sheet" || product.source === "api" ? ". Its details refresh on each sync unless you edit them here." : "."}
				</p>
			)}
			{!sku.trim() && !externalId.trim() && (
				<Alert><AlertDescription>{KEY_WARNING}</AlertDescription></Alert>
			)}
			<div className="grid gap-3 sm:grid-cols-2">
				<div className="space-y-1">
					<Label htmlFor="pe-name">Name</Label>
					<Input id="pe-name" value={name} onChange={(e) => setName(e.target.value)} />
					{!name.trim() && <p className="text-xs text-muted-foreground">Enter a product name.</p>}
				</div>
				<div className="space-y-1">
					<Label htmlFor="pe-price">Price ({currencySymbol(currency)})</Label>
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

			<div className="space-y-2">
				<Label>Photos</Label>
				<p className="text-sm text-muted-foreground">
					{product || savedId ? "Photos let the assistant recognise this product when a customer sends a picture." : "Photos upload when you add the product."}
				</p>
				{imageSearchOff && (
					<p className="text-sm text-muted-foreground">
						Image search is off, so the assistant won&apos;t match customer pictures yet.{" "}
						<Link href="/channels" className="text-primary underline">Turn it on in Channels.</Link>
					</p>
				)}
				<div className="flex flex-wrap gap-3">
					{images.map((img, i) => (
						<div key={img.id} className="w-24 space-y-1">
							{/* eslint-disable-next-line @next/next/no-img-element */}
							<img src={`${API_BASE_URL}${img.thumb_url}`} alt={product?.name ?? ""} loading="lazy" className="h-24 w-24 rounded-md border object-cover" />
							<div className="flex items-center justify-between gap-1">
								{img.status !== "ready" && (
									<Badge variant={img.status === "failed" ? "destructive" : "outline"}>{PHOTO_BADGE[img.status]}</Badge>
								)}
								<button
									type="button"
									aria-label={`Remove photo ${i + 1} of ${images.length} of ${product?.name ?? ""}`}
									className="ml-auto text-muted-foreground hover:text-destructive"
									onClick={() => removePhoto(img)}
								>
									<X className="h-4 w-4" />
								</button>
							</div>
						</div>
					))}
					{files.map((f, i) => (
						<div key={`${f.name}-${i}`} className="w-24 space-y-1">
							{/* eslint-disable-next-line @next/next/no-img-element */}
							<img src={previews[i]} alt={f.name} className="h-24 w-24 rounded-md border object-cover" />
							<div className="flex justify-end">
								<button
									type="button"
									aria-label={`Remove ${f.name}`}
									className="text-muted-foreground hover:text-destructive"
									onClick={() => setFiles((fs) => fs.filter((_, j) => j !== i))}
								>
									<X className="h-4 w-4" />
								</button>
							</div>
						</div>
					))}
					<Label className="cursor-pointer self-center">
						<Button type="button" variant="outline" size="sm" asChild>
							<span><Upload className="mr-2 h-4 w-4" />Add photos</span>
						</Button>
						<Input
							type="file"
							accept={IMAGE_ACCEPT}
							multiple
							className="hidden"
							onChange={(e) => {
								addPhotos(e.target.files);
								e.target.value = "";
							}}
						/>
					</Label>
				</div>
				{images.some((i) => i.status === "failed") && (
					<p className="text-sm text-destructive">This photo couldn&apos;t be processed. Remove it and add another.</p>
				)}
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

			<div className="flex flex-wrap items-center gap-2">
				<Button onClick={save} disabled={saving || !name.trim()}>
					{saving && <Loader2 className="h-4 w-4 animate-spin" />} {savedId === null ? "Add product" : "Save changes"}
				</Button>
				<Button variant="outline" onClick={onClose} disabled={saving}>Cancel</Button>
				{product && (
					<Button variant="destructive" className="ml-auto" onClick={remove} disabled={saving}>Delete product</Button>
				)}
			</div>
		</div>
	);
}
