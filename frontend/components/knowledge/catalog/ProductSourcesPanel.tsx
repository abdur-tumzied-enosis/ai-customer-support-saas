"use client";

import { useCallback, useEffect, useState } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Loader2 } from "lucide-react";
import {
	productSourceApi, type ApiConfig, type CsvPreview, type MappingSuggestion, type ProductSource, type SourceResult,
} from "@/lib/api";
import { formatError } from "@/lib/errors";
import ApiSourceForm from "./ApiSourceForm";

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
	{ key: "image_url", label: "Image URL" },
];

// Shown on every source so it is clear where the products come from
const SOURCE_TYPE: Record<ProductSource["kind"], string> = { sheet: "Google Sheet", api: "REST API", csv: "CSV or Excel file" };

// Below this the model is unsure: the suggestion is shown, never pre-applied
const UNSURE_BELOW = 0.6;

const NUMBER = /^-?[\d,]*\.?\d+$/;
const isWebLink = (v: string) => /^https?:\/\//i.test(v);

const formatTime = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : null);

// Connection status: an error wins, then whether a sync has ever succeeded
const connectionStatus = (s: ProductSource) => {
	if (s.last_error) return { label: "Error", tone: "text-destructive" };
	if (s.last_synced_at) return { label: "Connected", tone: "text-emerald-600" };
	return { label: "Not synced yet", tone: "text-muted-foreground" };
};

// Where a source's data comes from. The API key is never part of this.
const originOf = (s: ProductSource) => {
	if (s.kind === "sheet") return s.sheet_url;
	if (s.kind === "api") return `${s.api_config?.method ?? "GET"} ${s.api_config?.url ?? ""}`;
	return "Uploaded file";
};

// Plain checks on the example values: a warning, not a block (the owner decides)
const typeWarning = (target: string, column: string, sample: Record<string, string>[]): string | null => {
	const values = sample.map((r) => (r[column] ?? "").trim()).filter(Boolean);
	if (target === "price" && values.some((v) => !NUMBER.test(v.replace(/\s/g, "")))) return "Some examples are not plain numbers";
	if ((target === "image_url" || target === "product_url") && values.some((v) => !isWebLink(v))) return "Some examples are not web links";
	return null;
};

const firstValue = (column: string, sample: Record<string, string>[]) => sample.map((r) => (r[column] ?? "").trim()).find(Boolean) ?? "";

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
	// Which source the confirm step is for
	const [previewOf, setPreviewOf] = useState<"file" | "sheet" | "api">("file");
	// The REST request and its key, kept until the import (the key is re-sent on every re-preview)
	const [apiDraft, setApiDraft] = useState<ApiConfig | null>(null);
	const [apiSecret, setApiSecret] = useState("");
	const [formKey, setFormKey] = useState(0);
	const [suggestions, setSuggestions] = useState<MappingSuggestion[] | null>(null);

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
			text: `${r.created} added, ${r.updated} updated, ${r.unchanged} unchanged, ${r.hidden} hidden` +
				(r.images_queued ? `, ${r.images_queued} ${r.images_queued === 1 ? "image" : "images"} downloading` : ""),
			rejected: r.rejected,
		});
		onChanged();
	};

	const choose = (f: File | null) => {
		setFile(f);
		setPreview(null);
		setSuggestions(null);
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
		setSuggestions(null);
		setPreviewOf("sheet");
		run("preview", async () => {
			const p = await productSourceApi.previewSheet(projectId, kbId, sheetUrl.trim());
			setPreview(p);
			setMapping(p.mapping);
		});
	};

	const previewApi = (api: ApiConfig, secret: string) => {
		setFile(null);
		setPreview(null);
		setSuggestions(null);
		setPreviewOf("api");
		setApiDraft(api);
		setApiSecret(secret);
		run("preview", async () => {
			const p = await productSourceApi.previewApi(projectId, kbId, api, secret);
			setPreview(p);
			setMapping(p.mapping);
		});
	};

	// Re-ask the server with the chosen columns so key_warning and the sample follow them
	const refetch = (next: Record<string, string | null>): (() => Promise<CsvPreview>) | null => {
		if (previewOf === "sheet") return () => productSourceApi.previewSheet(projectId, kbId, sheetUrl.trim(), next);
		if (previewOf === "api" && apiDraft) return () => productSourceApi.previewApi(projectId, kbId, apiDraft, apiSecret, next);
		if (file) return () => productSourceApi.previewCsv(projectId, kbId, file, next);
		return null;
	};

	const remap = (target: string, col: string) => {
		const next = { ...mapping, [target]: col || null };
		setMapping(next);
		const again = refetch(next);
		if (again) run("preview", async () => setPreview(await again()));
	};

	const suggest = () => preview && run("suggest", async () => {
		setSuggestions(await productSourceApi.suggestMapping(projectId, kbId, preview.headers, preview.sample));
	});

	// A suggestion is applied only when the user accepts it
	const accept = (sg: MappingSuggestion) => {
		setSuggestions((list) => (list ?? []).filter((x) => x !== sg));
		remap(sg.target, sg.column);
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

	const addApi = () => apiDraft && run("api", async () => {
		const { source, result } = await productSourceApi.createApi(projectId, kbId, apiDraft, apiSecret, mapping);
		setApiDraft(null);
		setApiSecret("");
		setPreview(null);
		setFormKey((k) => k + 1); // clears the form, so the key is not left on screen
		setSources((s) => [source, ...(s ?? [])]);
		summarize(result);
	});

	const sync = (id: string) => run(id, async () => {
		const { source, result } = await productSourceApi.sync(projectId, kbId, id);
		setSources((s) => (s ?? []).map((x) => (x.id === id ? source : x)));
		summarize(result);
	});

	// Turning automatic sync on or changing its schedule; the server makes the next run due now
	const setSchedule = (id: string, body: { auto_sync_enabled?: boolean; schedule?: "hourly" | "daily" }) => run(id, async () => {
		const { source } = await productSourceApi.updateSchedule(projectId, kbId, id, body);
		setSources((s) => (s ?? []).map((x) => (x.id === id ? source : x)));
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
	const sample = preview?.sample ?? [];
	const importKind = previewOf === "sheet" ? "sheet" : previewOf === "api" ? "api" : "csv";
	const importAction = previewOf === "sheet" ? addSheet : previewOf === "api" ? addApi : importCsv;

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

			<section className="space-y-3">
				<h3 className="font-medium">Connect a REST API</h3>
				<p className="text-sm text-muted-foreground">An https endpoint that returns a JSON list of products. Keys stay on the server.</p>
				<ApiSourceForm key={formKey} busy={busy !== null} onPreview={previewApi} />
			</section>

			{preview && (
				<div className="space-y-3">
					<p className="text-sm text-muted-foreground">
						Importing from {previewOf === "sheet" ? "a Google Sheet" : previewOf === "api" ? "a REST API" : "a file"}.
						Confirm which column holds each detail.
					</p>
					<div className="grid gap-3 sm:grid-cols-2">
						{TARGETS.map((t) => {
							const col = mapping[t.key] ?? "";
							const example = col ? firstValue(col, sample) : "";
							const warning = col ? typeWarning(t.key, col, sample) : null;
							return (
								<label key={t.key} className="grid gap-1 text-sm">
									<span className="flex items-center justify-between gap-2">
										{t.label}
										<select
											className="h-9 rounded-md border bg-background px-2"
											value={col}
											onChange={(e) => remap(t.key, e.target.value)}
										>
											<option value="">Not in file</option>
											{headers.map((h) => <option key={h} value={h}>{h}</option>)}
										</select>
									</span>
									{example && <span className="truncate text-xs text-muted-foreground">e.g. {example}</span>}
									{warning && <span className="text-xs text-amber-600">{warning}</span>}
									{t.key === "image_url" && <span className="text-xs text-muted-foreground">Several links: separate them with commas or spaces</span>}
								</label>
							);
						})}
					</div>

					<div className="space-y-2">
						<Button variant="outline" size="sm" onClick={suggest} disabled={busy !== null}>
							{busy === "suggest" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}Suggest mapping with AI
						</Button>
						{suggestions && suggestions.length === 0 && <p className="text-sm text-muted-foreground">No suggestions. Map the columns above.</p>}
						{suggestions && suggestions.length > 0 && (
							<ul className="space-y-2">
								{suggestions.map((sg) => {
									const label = TARGETS.find((t) => t.key === sg.target)?.label ?? sg.target;
									const example = firstValue(sg.column, sample);
									return (
										<li key={`${sg.target}:${sg.column}`} className="flex flex-wrap items-center justify-between gap-2 rounded-md border p-2 text-sm">
											<span className="min-w-0">
												<b>{label}</b> ← {sg.column}
												<span className="text-muted-foreground"> · {Math.round(sg.confidence * 100)}%{example ? ` · e.g. ${example}` : ""}</span>
												{sg.reason && <span className="block text-xs text-muted-foreground">{sg.reason}</span>}
												{sg.confidence < UNSURE_BELOW && <span className="block text-xs text-amber-600">Unsure: check the examples before accepting</span>}
											</span>
											<span className="flex gap-2">
												<Button size="sm" onClick={() => accept(sg)} disabled={busy !== null}>Accept</Button>
												<Button size="sm" variant="outline" onClick={() => setSuggestions((l) => (l ?? []).filter((x) => x !== sg))}>Dismiss</Button>
											</span>
										</li>
									);
								})}
							</ul>
						)}
					</div>

					{preview.key_warning && <Alert><AlertDescription>{KEY_WARNING}</AlertDescription></Alert>}
					<Button onClick={importAction} disabled={!mapping.name || busy !== null}>
						{busy === importKind && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
						Import {previewOf === "sheet" ? "sheet" : previewOf === "api" ? "REST API" : file?.name}
					</Button>
				</div>
			)}

			<section className="space-y-2">
				<h3 className="font-medium">Sources</h3>
				{sources === null ? null : sources.length === 0 ? (
					<p className="text-sm text-muted-foreground">No sources yet</p>
				) : sources.map((s) => {
					const status = connectionStatus(s);
					const mapped = TARGETS.filter((t) => s.mapping[t.key]).length;
					const scheduled = s.kind !== "csv";
					return (
						<div key={s.id} className="space-y-2 rounded-md border p-3 text-sm">
							<div className="flex flex-wrap items-center justify-between gap-2">
								<div className="flex min-w-0 items-center gap-2">
									<Badge variant="secondary">{SOURCE_TYPE[s.kind]}</Badge>
									<span className="min-w-0 truncate">{originOf(s)}</span>
									{s.has_secret && <span className="shrink-0 text-xs text-muted-foreground">Key stored</span>}
								</div>
								<span className="flex shrink-0 gap-2">
									{scheduled && (
										<Button size="sm" variant="outline" disabled={busy !== null || s.sync_running} onClick={() => sync(s.id)}>
											{busy === s.id && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
											{s.sync_running ? "Syncing..." : "Sync now"}
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
								<span className={status.tone}>{status.label}</span>
								{" · "}{s.last_synced_at ? `Last synced ${formatTime(s.last_synced_at)}` : "Not synced yet"}
								{" · "}{mapped} of {TARGETS.length} fields mapped
								{" · "}{s.mapping.image_url ? "Images from the Image URL column" : "No image column"}
							</p>

							{scheduled && (
								<div className="flex flex-wrap items-center gap-3">
									<label className="flex items-center gap-2">
										<input
											type="checkbox"
											checked={s.auto_sync_enabled}
											disabled={busy !== null}
											onChange={(e) => setSchedule(s.id, { auto_sync_enabled: e.target.checked })}
										/>
										Automatic sync
									</label>
									<select
										className="h-8 rounded-md border bg-background px-2"
										value={s.schedule}
										disabled={busy !== null || !s.auto_sync_enabled}
										onChange={(e) => setSchedule(s.id, { schedule: e.target.value as "hourly" | "daily" })}
									>
										<option value="hourly">Hourly</option>
										<option value="daily">Daily</option>
									</select>
									{s.auto_sync_enabled && s.next_sync_at && <span className="text-muted-foreground">Next sync {formatTime(s.next_sync_at)}</span>}
									{s.sync_failures > 0 && <span className="text-amber-600">{s.sync_failures} failed {s.sync_failures === 1 ? "try" : "tries"} in a row; retrying</span>}
								</div>
							)}

							{s.last_error && <p className="text-destructive">{s.last_error}</p>}
							{s.key_warning && <p className="text-amber-600">{KEY_WARNING}</p>}
						</div>
					);
				})}
			</section>
		</div>
	);
}
