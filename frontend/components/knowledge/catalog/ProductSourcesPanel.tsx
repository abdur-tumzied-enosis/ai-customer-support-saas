"use client";

import { useCallback, useEffect, useState } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Loader2 } from "lucide-react";
import { productSourceApi, type CsvPreview, type ProductSource, type SourceResult } from "@/lib/api";
import { formatError } from "@/lib/errors";

export const KEY_WARNING =
	"Add your product ID or SKU so updates match the right product. Without it we match by name, which can mix up similar products.";

const TARGETS: { key: string; label: string }[] = [
	{ key: "name", label: "Name" },
	{ key: "external_id", label: "Product ID" },
	{ key: "sku", label: "SKU" },
	{ key: "price", label: "Price" },
	{ key: "description", label: "Description" },
	{ key: "available", label: "Available" },
	{ key: "variant_label", label: "Variant" },
	{ key: "product_url", label: "Product link" },
];

type Props = { projectId: string; kbId: string; onChanged: () => void };

export default function ProductSourcesPanel({ projectId, kbId, onChanged }: Props) {
	const [sources, setSources] = useState<ProductSource[] | null>(null);
	const [error, setError] = useState<string | null>(null);
	const [notice, setNotice] = useState<{ text: string; rejected: SourceResult["rejected"] } | null>(null);
	const [busy, setBusy] = useState<string | null>(null);
	const [file, setFile] = useState<File | null>(null);
	const [preview, setPreview] = useState<CsvPreview | null>(null);
	const [mapping, setMapping] = useState<Record<string, string | null>>({});
	const [sheetUrl, setSheetUrl] = useState("");
	// Which source the confirm step is for: the chosen file, or the sheet URL that was previewed
	const [previewOf, setPreviewOf] = useState<"file" | "sheet">("file");

	const load = useCallback(async () => {
		try {
			setSources(await productSourceApi.list(projectId, kbId));
		} catch (err) {
			setError(formatError(err));
		}
	}, [projectId, kbId]);

	useEffect(() => { load(); }, [load]);

	// One wrapper so every action shows its busy state and error the same way
	const run = async (id: string, fn: () => Promise<void>) => {
		setBusy(id);
		setError(null);
		try {
			await fn();
		} catch (err) {
			setError(formatError(err));
		} finally {
			setBusy(null);
		}
	};

	const summarize = (r: SourceResult) => {
		setNotice({
			text: `${r.created} added, ${r.updated} updated, ${r.unchanged} unchanged, ${r.hidden} hidden`,
			rejected: r.rejected,
		});
		onChanged();
	};

	const choose = (f: File | null) => {
		setFile(f);
		setPreview(null);
		setPreviewOf("file");
		if (f) run("preview", async () => {
			const p = await productSourceApi.previewCsv(projectId, kbId, f);
			setPreview(p);
			setMapping(p.mapping);
		});
	};

	const previewSheet = () => {
		setFile(null);
		setPreview(null);
		setPreviewOf("sheet");
		run("preview", async () => {
			const p = await productSourceApi.previewSheet(projectId, kbId, sheetUrl.trim());
			setPreview(p);
			setMapping(p.mapping);
		});
	};

	// Re-ask the server so key_warning follows the chosen columns
	const remap = (target: string, col: string) => {
		const next = { ...mapping, [target]: col || null };
		setMapping(next);
		if (previewOf === "sheet") run("preview", async () => setPreview(await productSourceApi.previewSheet(projectId, kbId, sheetUrl.trim(), next)));
		else if (file) run("preview", async () => setPreview(await productSourceApi.previewCsv(projectId, kbId, file, next)));
	};

	const importCsv = () => file && run("csv", async () => {
		const { source, result } = await productSourceApi.createCsv(projectId, kbId, file, mapping);
		setFile(null);
		setPreview(null);
		setSources((s) => [source, ...(s ?? [])]);
		summarize(result);
	});

	const addSheet = () => run("sheet", async () => {
		const { source, result } = await productSourceApi.createSheet(projectId, kbId, sheetUrl.trim(), mapping);
		setSheetUrl("");
		setPreview(null);
		setSources((s) => [source, ...(s ?? [])]);
		summarize(result);
	});

	const sync = (id: string) => run(id, async () => {
		const { source, result } = await productSourceApi.sync(projectId, kbId, id);
		setSources((s) => (s ?? []).map((x) => (x.id === id ? source : x)));
		summarize(result);
	});

	const reupload = (id: string, f: File) => run(id, async () => {
		const { source, result } = await productSourceApi.reupload(projectId, kbId, id, f);
		setSources((s) => (s ?? []).map((x) => (x.id === id ? source : x)));
		summarize(result);
	});

	const remove = (id: string) => {
		if (!window.confirm("Remove this source? Its products stay in your catalog.")) return;
		run(id, async () => {
			await productSourceApi.remove(projectId, kbId, id);
			setSources((s) => (s ?? []).filter((x) => x.id !== id));
		});
	};

	const headers = preview?.headers ?? [];

	return (
		<div className="space-y-6">
			{error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
			{notice && (
				<Alert>
					<AlertDescription>
						{notice.text}
						{notice.rejected.length > 0 && (
							<ul className="mt-2 list-disc pl-5">
								{notice.rejected.map((r, i) => <li key={i}>Row {r.row}: {r.message}</li>)}
							</ul>
						)}
					</AlertDescription>
				</Alert>
			)}

			<section className="space-y-3">
				<h3 className="font-medium">Upload a CSV or Excel file</h3>
				<Input type="file" accept=".csv,.xlsx" key={sources?.length} onChange={(e) => choose(e.target.files?.[0] ?? null)} />
				{busy === "preview" && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}
			</section>

			<section className="space-y-3">
				<h3 className="font-medium">Link a Google Sheet</h3>
				<p className="text-sm text-muted-foreground">The sheet must be shared as &quot;Anyone with the link can view&quot;.</p>
				<div className="flex gap-2">
					<Input value={sheetUrl} onChange={(e) => {
							setSheetUrl(e.target.value);
							// a new URL needs its own preview: never import it with the old one's mapping
							if (previewOf === "sheet") setPreview(null);
						}} placeholder="https://docs.google.com/spreadsheets/..." />
					<Button onClick={previewSheet} disabled={!sheetUrl.trim() || busy !== null}>
						Preview
					</Button>
				</div>
			</section>

			{preview && (
				<div className="space-y-3">
					<p className="text-sm text-muted-foreground">Confirm which column holds each detail.</p>
					<div className="grid gap-2 sm:grid-cols-2">
						{TARGETS.map((t) => (
							<label key={t.key} className="flex items-center justify-between gap-2 text-sm">
								{t.label}
								<select
									className="h-9 rounded-md border bg-background px-2"
									value={mapping[t.key] ?? ""}
									onChange={(e) => remap(t.key, e.target.value)}
								>
									<option value="">Not in file</option>
									{headers.map((h) => <option key={h} value={h}>{h}</option>)}
								</select>
							</label>
						))}
					</div>
					{preview.key_warning && <Alert><AlertDescription>{KEY_WARNING}</AlertDescription></Alert>}
					<Button onClick={previewOf === "sheet" ? addSheet : importCsv} disabled={!mapping.name || busy !== null}>
						{busy === (previewOf === "sheet" ? "sheet" : "csv") && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}Import {previewOf === "sheet" ? "sheet" : file?.name}
					</Button>
				</div>
			)}

			<section className="space-y-2">
				<h3 className="font-medium">Sources</h3>
				{sources === null ? null : sources.length === 0 ? (
					<p className="text-sm text-muted-foreground">No sources yet</p>
				) : sources.map((s) => (
					<div key={s.id} className="space-y-1 rounded-md border p-3 text-sm">
						<div className="flex items-center justify-between gap-2">
							<span className="min-w-0 truncate">{s.kind === "sheet" ? s.sheet_url : "CSV or Excel file"}</span>
							<span className="flex shrink-0 gap-2">
								{s.kind === "sheet" && (
									<Button size="sm" variant="outline" disabled={busy !== null} onClick={() => sync(s.id)}>
										{busy === s.id && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}Sync now
									</Button>
								)}
								{s.kind === "csv" && (
									<label className={buttonVariants({ size: "sm", variant: "outline", className: "cursor-pointer focus-within:ring-2 focus-within:ring-ring" })}>
										{busy === s.id && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}Upload new version
										<input
											type="file"
											accept=".csv,.xlsx"
											className="sr-only"
											disabled={busy !== null}
											onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) reupload(s.id, f); }}
										/>
									</label>
								)}
								<Button size="sm" variant="outline" disabled={busy !== null} onClick={() => remove(s.id)}>Remove</Button>
							</span>
						</div>
						<p className="text-muted-foreground">
							{s.last_synced_at ? `Last synced ${new Date(s.last_synced_at).toLocaleString()}` : "Not synced yet"}
						</p>
						{s.last_error && <p className="text-destructive">{s.last_error}</p>}
						{s.key_warning && <p className="text-amber-600">{KEY_WARNING}</p>}
					</div>
				))}
			</section>
		</div>
	);
}
