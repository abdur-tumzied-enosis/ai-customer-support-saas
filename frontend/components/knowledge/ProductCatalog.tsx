"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Loader2, Pencil, Plus, Trash2, Upload, X } from "lucide-react";
import { API_BASE_URL, productApi, type Product } from "@/lib/api";
import { formatError } from "@/lib/errors";

const IMAGE_ACCEPT = "image/jpeg,image/png,image/webp";
const POLL_MS = 3000;

type Props = { projectId: string; kbId: string; isAdmin: boolean };

export default function ProductCatalog({ projectId, kbId, isAdmin }: Props) {
	const [products, setProducts] = useState<Product[]>([]);
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState<string | null>(null);

	const [name, setName] = useState("");
	const [description, setDescription] = useState("");
	const [files, setFiles] = useState<File[]>([]);
	const [creating, setCreating] = useState(false);
	const fileInput = useRef<HTMLInputElement>(null);

	const [editingId, setEditingId] = useState<string | null>(null);
	const [editName, setEditName] = useState("");
	const [editDescription, setEditDescription] = useState("");
	const [busyId, setBusyId] = useState<string | null>(null);

	const load = useCallback(async () => {
		try {
			setProducts(await productApi.list(projectId, kbId));
		} catch (err) {
			setError(formatError(err));
		} finally {
			setLoading(false);
		}
	}, [projectId, kbId]);

	useEffect(() => {
		setLoading(true);
		load();
	}, [load]);

	const hasPending = products.some((p) => p.images.some((i) => i.status === "pending"));
	useEffect(() => {
		if (!hasPending) return;
		const timer = setInterval(load, POLL_MS);
		return () => clearInterval(timer);
	}, [hasPending, load]);

	const handleCreate = async (e: React.FormEvent) => {
		e.preventDefault();
		if (!name.trim()) return;
		setCreating(true);
		setError(null);
		try {
			await productApi.create(projectId, kbId, name.trim(), description.trim(), files);
			setName("");
			setDescription("");
			setFiles([]);
			if (fileInput.current) fileInput.current.value = "";
			await load();
		} catch (err) {
			setError(formatError(err));
		} finally {
			setCreating(false);
		}
	};

	const run = async (id: string, action: () => Promise<unknown>) => {
		setBusyId(id);
		setError(null);
		try {
			await action();
			await load();
		} catch (err) {
			setError(formatError(err));
		} finally {
			setBusyId(null);
		}
	};

	const startEdit = (p: Product) => {
		setEditingId(p.id);
		setEditName(p.name);
		setEditDescription(p.description);
	};

	const saveEdit = (id: string) =>
		run(id, async () => {
			await productApi.update(id, { name: editName.trim(), description: editDescription.trim() });
			setEditingId(null);
		});

	const removeProduct = (p: Product) => {
		if (!window.confirm(`Delete "${p.name}" and its photos?`)) return;
		run(p.id, () => productApi.remove(p.id));
	};

	const addImages = (id: string, list: FileList | null) => {
		if (!list || list.length === 0) return;
		run(id, () => productApi.addImages(id, Array.from(list)));
	};

	const statusVariant = (s: string) => (s === "failed" ? "destructive" : s === "ready" ? "secondary" : "outline");

	return (
		<div className="space-y-4">
			{error && (
				<Alert variant="destructive">
					<AlertDescription>{error}</AlertDescription>
				</Alert>
			)}

			{isAdmin && (
			<form onSubmit={handleCreate} className="space-y-3 rounded-md border p-4">
				<div className="space-y-2">
					<Label htmlFor="product-name">Product name</Label>
					<Input id="product-name" value={name} onChange={(e) => setName(e.target.value)} required />
				</div>
				<div className="space-y-2">
					<Label htmlFor="product-description">Description</Label>
					<Textarea
						id="product-description"
						value={description}
						onChange={(e) => setDescription(e.target.value)}
						rows={2}
					/>
				</div>
				<div className="space-y-2">
					<Label htmlFor="product-images">Photos</Label>
					<Input
						id="product-images"
						ref={fileInput}
						type="file"
						accept={IMAGE_ACCEPT}
						multiple
						onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
					/>
				</div>
				<Button type="submit" disabled={creating || !name.trim()}>
					{creating ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Plus className="mr-2 h-4 w-4" />}
					{creating ? "Adding..." : "Add product"}
				</Button>
			</form>
			)}

			{loading ? (
				<div className="flex justify-center py-4">
					<Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
				</div>
			) : products.length === 0 ? (
				<p className="text-center py-4 text-muted-foreground">No products yet</p>
			) : (
				<div className="space-y-3">
					{products.map((p) => (
						<div key={p.id} className="rounded-md border p-4 space-y-3">
							{isAdmin && editingId === p.id ? (
								<div className="space-y-2">
									<Input value={editName} onChange={(e) => setEditName(e.target.value)} />
									<Textarea value={editDescription} onChange={(e) => setEditDescription(e.target.value)} rows={2} />
									<div className="flex gap-2">
										<Button size="sm" disabled={busyId === p.id || !editName.trim()} onClick={() => saveEdit(p.id)}>
											Save
										</Button>
										<Button size="sm" variant="outline" onClick={() => setEditingId(null)}>
											Cancel
										</Button>
									</div>
								</div>
							) : (
								<div className="flex items-start justify-between gap-2">
									<div>
										<p className="font-medium">{p.name}</p>
										{p.description && <p className="text-sm text-muted-foreground">{p.description}</p>}
									</div>
									{isAdmin && (
									<div className="flex gap-1">
										<Button size="sm" variant="ghost" aria-label="Edit product" onClick={() => startEdit(p)}>
											<Pencil className="h-4 w-4" />
										</Button>
										<Button
											size="sm"
											variant="ghost"
											aria-label="Delete product"
											disabled={busyId === p.id}
											onClick={() => removeProduct(p)}
										>
											<Trash2 className="h-4 w-4" />
										</Button>
									</div>
									)}
								</div>
							)}

							<div className="flex flex-wrap gap-3">
								{p.images.map((img) => (
									<div key={img.id} className="w-24 space-y-1">
										{/* eslint-disable-next-line @next/next/no-img-element */}
										<img
											src={`${API_BASE_URL}${img.url}`}
											alt={p.name}
											className="h-24 w-24 rounded-md border object-cover"
										/>
										<div className="flex items-center justify-between">
											<Badge variant={statusVariant(img.status)}>{img.status}</Badge>
											{isAdmin && (
											<button
												type="button"
												aria-label="Delete photo"
												className="text-muted-foreground hover:text-destructive"
												disabled={busyId === p.id}
												onClick={() => run(p.id, () => productApi.removeImage(img.id))}
											>
												<X className="h-4 w-4" />
											</button>
											)}
										</div>
									</div>
								))}
								{isAdmin && (
								<Label className="cursor-pointer self-center">
									<Button type="button" variant="outline" size="sm" asChild disabled={busyId === p.id}>
										<span>
											<Upload className="mr-2 h-4 w-4" />
											Add photos
										</span>
									</Button>
									<Input
										type="file"
										accept={IMAGE_ACCEPT}
										multiple
										className="hidden"
										onChange={(e) => {
											addImages(p.id, e.target.files);
											e.target.value = "";
										}}
									/>
								</Label>
								)}
							</div>
						</div>
					))}
				</div>
			)}
		</div>
	);
}
