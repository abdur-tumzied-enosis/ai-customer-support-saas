"use client";

import { useState, type ChangeEvent } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { ApiConfig } from "@/lib/api";

type Draft = {
	method: "GET" | "POST";
	url: string;
	headers: string;
	query: string;
	body: string;
	auth_type: ApiConfig["auth_type"];
	auth_name: string;
	response_path: string;
	page_param: string;
	max_pages: string;
};

const EMPTY: Draft = {
	method: "GET", url: "", headers: "", query: "", body: "", auth_type: "none", auth_name: "",
	response_path: "", page_param: "", max_pages: "1",
};

// One "name<sep>value" per line; blank lines are skipped
function pairs(text: string, sep: string): Record<string, string> {
	const out: Record<string, string> = {};
	for (const raw of text.split("\n")) {
		const line = raw.trim();
		if (!line) continue;
		const at = line.indexOf(sep);
		if (at < 1) throw new Error(`Write each line as name${sep === ":" ? ": value" : "=value"}: ${line}`);
		out[line.slice(0, at).trim()] = line.slice(at + 1).trim();
	}
	return out;
}

export function toApiConfig(d: Draft): ApiConfig {
	let body: Record<string, unknown> | null = null;
	if (d.method === "POST" && d.body.trim()) {
		try {
			body = JSON.parse(d.body);
		} catch {
			throw new Error("The request body must be valid JSON");
		}
	}
	return {
		method: d.method,
		url: d.url.trim(),
		headers: pairs(d.headers, ":"),
		query: pairs(d.query, "="),
		auth_type: d.auth_type,
		auth_name: d.auth_name.trim(),
		body,
		response_path: d.response_path.trim(),
		page_param: d.page_param.trim(),
		max_pages: Number(d.max_pages) || 1,
	};
}

const AREA = "min-h-16 w-full rounded-md border bg-background p-2 text-sm";
const SELECT = "h-9 w-full rounded-md border bg-background px-2 text-sm";

type Props = { busy: boolean; onPreview: (api: ApiConfig, secret: string) => void };

// Only builds the request and hands it up: the panel runs the preview and keeps the secret for the import
export default function ApiSourceForm({ busy, onPreview }: Props) {
	const [d, setD] = useState<Draft>(EMPTY);
	const [secret, setSecret] = useState("");
	const [error, setError] = useState<string | null>(null);
	const set = (key: keyof Draft) => (e: ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) =>
		setD({ ...d, [key]: e.target.value });

	const submit = () => {
		setError(null);
		try {
			onPreview(toApiConfig(d), secret);
		} catch (err) {
			setError(err instanceof Error ? err.message : String(err));
		}
	};

	return (
		<div className="space-y-3">
			<div className="grid gap-2 sm:grid-cols-[120px_1fr]">
				<label className="grid gap-1 text-sm">Method
					<select className={SELECT} value={d.method} onChange={set("method")}>
						<option value="GET">GET</option>
						<option value="POST">POST</option>
					</select>
				</label>
				<label className="grid gap-1 text-sm">Endpoint URL (https)
					<Input value={d.url} onChange={set("url")} placeholder="https://api.example.com/v1/products" />
				</label>
			</div>

			<div className="grid gap-2 sm:grid-cols-2">
				<label className="grid gap-1 text-sm">Headers (one per line: Name: value)
					<textarea className={AREA} value={d.headers} onChange={set("headers")} placeholder="Accept: application/json" />
				</label>
				<label className="grid gap-1 text-sm">Query parameters (one per line: name=value)
					<textarea className={AREA} value={d.query} onChange={set("query")} placeholder="currency=BDT" />
				</label>
			</div>

			<div className="grid gap-2 sm:grid-cols-3">
				<label className="grid gap-1 text-sm">Authentication
					<select className={SELECT} value={d.auth_type} onChange={set("auth_type")}>
						<option value="none">None</option>
						<option value="bearer">Bearer token</option>
						<option value="header">API key in a header</option>
						<option value="query">API key in a query parameter</option>
					</select>
				</label>
				{(d.auth_type === "header" || d.auth_type === "query") && (
					<label className="grid gap-1 text-sm">{d.auth_type === "header" ? "Header name" : "Parameter name"}
						<Input value={d.auth_name} onChange={set("auth_name")} placeholder={d.auth_type === "header" ? "X-API-Key" : "api_key"} />
					</label>
				)}
				{d.auth_type !== "none" && (
					<label className="grid gap-1 text-sm">Key or token
						<Input type="password" autoComplete="off" value={secret} onChange={(e) => setSecret(e.target.value)} />
					</label>
				)}
			</div>
			<p className="text-xs text-muted-foreground">The key is stored encrypted on the server and never shown again.</p>

			<div className="grid gap-2 sm:grid-cols-3">
				<label className="grid gap-1 text-sm">Where the product list is
					<Input value={d.response_path} onChange={set("response_path")} placeholder="data.items (empty = top level)" />
				</label>
				<label className="grid gap-1 text-sm">Page parameter (optional)
					<Input value={d.page_param} onChange={set("page_param")} placeholder="page" />
				</label>
				<label className="grid gap-1 text-sm">Max pages
					<Input type="number" min={1} max={20} value={d.max_pages} onChange={set("max_pages")} disabled={!d.page_param} />
				</label>
			</div>

			{d.method === "POST" && (
				<label className="grid gap-1 text-sm">Request body (JSON object)
					<textarea className={AREA} value={d.body} onChange={set("body")} placeholder='{"status": "active"}' />
				</label>
			)}

			{error && <p className="text-sm text-destructive">{error}</p>}
			<Button onClick={submit} disabled={!d.url.trim() || busy}>Test and preview</Button>
		</div>
	);
}
