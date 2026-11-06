"use client";

import { useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Loader2 } from "lucide-react";
import type { Product } from "@/lib/api";
import { formatError } from "@/lib/errors";

export const currencySymbol = (code: string) => (code === "BDT" ? "৳" : code);

type Props = {
	products: Product[];
	/** Throws on failure; the table shows the message under the row */
	onSavePrice: (product: Product, price: string) => Promise<void>;
	onEdit: (product: Product) => void;
};

export default function ProductTable({ products, onSavePrice, onEdit }: Props) {
	const inputs = useRef(new Map<string, HTMLInputElement>());
	const [drafts, setDrafts] = useState<Record<string, string>>({});
	const [busyId, setBusyId] = useState<string | null>(null);
	const [errors, setErrors] = useState<Record<string, string>>({});

	const without = (rec: Record<string, string>, id: string) => Object.fromEntries(Object.entries(rec).filter(([k]) => k !== id));

	const save = async (p: Product, i: number) => {
		const draft = drafts[p.id];
		const next = products.slice(i + 1).find((q) => q.status === "needs_info");
		if (draft === undefined || draft.trim() === (p.price ?? "")) {
			if (next) inputs.current.get(next.id)?.focus();
			return;
		}
		setBusyId(p.id);
		setErrors((e) => without(e, p.id));
		try {
			await onSavePrice(p, draft.trim());
			setDrafts((d) => without(d, p.id));
			if (next) inputs.current.get(next.id)?.focus();
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
					<TableHead>Name</TableHead>
					<TableHead>SKU / ID</TableHead>
					<TableHead className="w-40">Price</TableHead>
					<TableHead>Status</TableHead>
					<TableHead />
				</TableRow>
			</TableHeader>
			<TableBody>
				{products.map((p, i) => (
					<TableRow key={p.id}>
						<TableCell className="font-medium">
							{p.name}
							{errors[p.id] && <p className="text-xs text-destructive" role="alert">{errors[p.id]}</p>}
						</TableCell>
						<TableCell className="text-muted-foreground">{p.sku || p.external_id || "—"}</TableCell>
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
									value={drafts[p.id] ?? p.price ?? ""}
									disabled={busyId === p.id}
									onChange={(e) => setDrafts((d) => ({ ...d, [p.id]: e.target.value }))}
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
						<TableCell className="text-right">
							<Button variant="outline" size="sm" onClick={() => onEdit(p)}>Edit</Button>
						</TableCell>
					</TableRow>
				))}
			</TableBody>
		</Table>
	);
}
