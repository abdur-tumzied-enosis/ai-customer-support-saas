"use client";

import { useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Loader2 } from "lucide-react";
import { API_BASE_URL, type Product } from "@/lib/api";
import { formatError } from "@/lib/errors";
import { cn } from "@/lib/utils";

export const currencySymbol = (code: string) => (code === "BDT" ? "৳" : code);

type Props = {
	products: Product[];
	isAdmin: boolean;
	/** Throws on failure; the table shows the message under the row */
	onSavePrice: (product: Product, price: string) => Promise<void>;
	onEdit: (product: Product) => void;
	/** Setup flow: hide SKU and Edit below the sm breakpoint, and save a price when its field loses focus */
	compact?: boolean;
	saveOnBlur?: boolean;
};

function Thumb({ p }: { p: Product }) {
	const cover = p.images.find((i) => i.status === "ready");
	if (cover) {
		// eslint-disable-next-line @next/next/no-img-element
		return <img src={`${API_BASE_URL}${cover.thumb_url}`} alt={p.name} loading="lazy" className="h-10 w-10 rounded object-cover" />;
	}
	if (p.images.some((i) => i.status === "uploading" || i.status === "pending")) {
		return (
			<span role="img" aria-label="Indexing photo" className="flex h-10 w-10 items-center justify-center rounded border">
				<Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
			</span>
		);
	}
	const failed = p.images.some((i) => i.status === "failed");
	return <span role="img" aria-label={failed ? "Photo failed" : "No photo yet"} className="block h-10 w-10 rounded border bg-muted" />;
}

export default function ProductTable({ products, isAdmin, onSavePrice, onEdit, compact = false, saveOnBlur = false }: Props) {
	const inputs = useRef(new Map<string, HTMLInputElement>());
	const [drafts, setDrafts] = useState<Record<string, string>>({});
	const [busyId, setBusyId] = useState<string | null>(null);
	const [errors, setErrors] = useState<Record<string, string>>({});

	const without = (rec: Record<string, string>, id: string) => Object.fromEntries(Object.entries(rec).filter(([k]) => k !== id));

	// Enter moves on to the next empty price; leaving the field (blur) only saves, so focus is not taken away
	const save = async (p: Product, i: number, moveNext = true) => {
		const draft = drafts[p.id];
		const next = products.slice(i + 1).find((q) => q.status === "needs_info");
		if (draft === undefined || draft.trim() === (p.price ?? "")) {
			if (moveNext && next) inputs.current.get(next.id)?.focus();
			return;
		}
		setBusyId(p.id);
		setErrors((e) => without(e, p.id));
		try {
			await onSavePrice(p, draft.trim());
			setDrafts((d) => without(d, p.id));
			if (moveNext && next) inputs.current.get(next.id)?.focus();
		} catch (err) {
			setErrors((e) => ({ ...e, [p.id]: formatError(err) }));
		} finally {
			setBusyId(null);
		}
	};

	if (products.length === 0) return <p className="text-center py-4 text-muted-foreground">No products here</p>;

	return (
		<Table>
			<TableHeader>
				<TableRow>
					<TableHead className="w-14">Photo</TableHead>
					<TableHead>Name</TableHead>
					<TableHead className={compact ? "hidden sm:table-cell" : undefined}>SKU / ID</TableHead>
					<TableHead className="w-40">Price</TableHead>
					<TableHead>Status</TableHead>
					<TableHead className={compact ? "hidden sm:table-cell" : undefined} />
				</TableRow>
			</TableHeader>
			<TableBody>
				{products.map((p, i) => (
					<TableRow key={p.id}>
						<TableCell><Thumb p={p} /></TableCell>
						<TableCell className="font-medium">
							{p.name}
							{errors[p.id] && <p className="text-xs text-destructive" role="alert">{errors[p.id]}</p>}
						</TableCell>
						<TableCell className={cn("text-muted-foreground", compact && "hidden sm:table-cell")}>{p.sku || p.external_id || "—"}</TableCell>
						<TableCell>
							<div className="flex items-center gap-1">
								<span className="text-muted-foreground">{currencySymbol(p.currency)}</span>
								<Input
									ref={(el) => {
										if (el) inputs.current.set(p.id, el);
										else inputs.current.delete(p.id);
									}}
									inputMode="decimal"
									aria-label={`Price for ${p.name}`}
									data-needs-price={p.status === "needs_info" ? "true" : undefined}
									value={drafts[p.id] ?? p.price ?? ""}
									disabled={busyId === p.id}
									onChange={(e) => setDrafts((d) => ({ ...d, [p.id]: e.target.value }))}
									onBlur={saveOnBlur ? () => void save(p, i, false) : undefined}
									onKeyDown={(e) => {
										if (e.key === "Enter") {
											e.preventDefault();
											save(p, i);
										}
									}}
									className="h-8"
								/>
								{busyId === p.id && <Loader2 className="h-4 w-4 animate-spin" />}
							</div>
						</TableCell>
						<TableCell>
							<Badge variant={p.status === "needs_info" ? "destructive" : "secondary"}>
								{p.status === "needs_info" ? "Needs info" : p.status === "hidden" ? "Hidden" : "Active"}
							</Badge>
						</TableCell>
						<TableCell className={cn("text-right", compact && "hidden sm:table-cell")}>
							{isAdmin && <Button variant="outline" size="sm" onClick={() => onEdit(p)}>Edit</Button>}
						</TableCell>
					</TableRow>
				))}
			</TableBody>
		</Table>
	);
}
