"use client";

import { Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { ApiDomain } from "@/lib/api";

export type ApiAccessValue = { api_tool_enabled: boolean; api_domains: ApiDomain[] };

function hostnameOf(value: string): string {
	const v = value.trim();
	try {
		return new URL(v.includes("://") ? v : `https://${v}`).hostname;
	} catch {
		return v.toLowerCase();
	}
}

/** Drop empty rows, reduce pasted URLs to hostnames, never send has_secret or blank secrets. */
export function toApiDomainsPayload(domains: ApiDomain[]): ApiDomain[] {
	return domains
		.filter((d) => d.domain.trim())
		.map((d) => ({
			domain: hostnameOf(d.domain),
			...(d.auth_header?.trim() ? { auth_header: d.auth_header.trim() } : {}),
			...(d.secret?.trim() ? { secret: d.secret.trim() } : {}),
		}));
}

const EXAMPLE = (domain: string) =>
	`When the customer asks about an order, ask for their order number, then call GET https://${domain}/orders/{order_number} and summarize the status in one or two sentences. If the call fails, apologize and offer to connect them with a person.`;

export function ApiAccessEditor({
	value,
	message,
	onChange,
	onInsertExample,
}: {
	value: ApiAccessValue;
	message: string;
	onChange: (value: ApiAccessValue) => void;
	onInsertExample: (text: string) => void;
}) {
	const { api_tool_enabled: enabled, api_domains: domains } = value;
	const setDomains = (next: ApiDomain[]) => onChange({ ...value, api_domains: next });
	const update = (i: number, patch: Partial<ApiDomain>) =>
		setDomains(domains.map((d, j) => (j === i ? { ...d, ...patch } : d)));

	const insecure = domains.filter(
		(d) => (d.secret || d.has_secret) && d.domain.trim() && message.includes(`http://${hostnameOf(d.domain)}`)
	);

	return (
		<details className="rounded-md border p-3" open={enabled}>
			<summary className="cursor-pointer text-sm font-medium flex items-center justify-between">
				<span>API access</span>
				<label className="flex items-center gap-2 text-sm font-normal" onClick={(e) => e.stopPropagation()}>
					<input
						type="checkbox"
						checked={enabled}
						onChange={(e) =>
							onChange({
								api_tool_enabled: e.target.checked,
								api_domains: e.target.checked && domains.length === 0 ? [{ domain: "" }] : domains,
							})
						}
					/>
					Enable API calls
				</label>
			</summary>

			<div className="mt-3 space-y-3">
				<p className="text-xs text-muted-foreground">
					The bot can make GET/POST calls only to these domains (and their subdomains). Use a low-privilege API
					key. Describe which URLs and fields to use in the message above.
				</p>

				{domains.map((d, i) => (
					<div key={i} className="grid grid-cols-1 sm:grid-cols-[1fr_1fr_1fr_auto] gap-2 items-end">
						<div className="space-y-1">
							<Label className="text-xs">Domain</Label>
							<Input
								placeholder="shop.com"
								value={d.domain}
								onChange={(e) => update(i, { domain: e.target.value })}
								onBlur={(e) => e.target.value && update(i, { domain: hostnameOf(e.target.value) })}
							/>
						</div>
						<div className="space-y-1">
							<Label className="text-xs">Auth header (optional)</Label>
							<Input
								placeholder="Authorization"
								value={d.auth_header ?? ""}
								onChange={(e) => update(i, { auth_header: e.target.value })}
							/>
						</div>
						<div className="space-y-1">
							<Label className="text-xs">Secret (optional)</Label>
							<Input
								type="password"
								autoComplete="new-password"
								placeholder={d.has_secret ? "(set) leave blank to keep" : "Bearer sk_..."}
								value={d.secret ?? ""}
								onChange={(e) => update(i, { secret: e.target.value })}
							/>
						</div>
						<Button
							type="button"
							variant="ghost"
							size="icon"
							aria-label={`Remove ${d.domain || "domain"}`}
							onClick={() => setDomains(domains.filter((_, j) => j !== i))}
						>
							<Trash2 className="h-4 w-4" />
						</Button>
					</div>
				))}

				<div className="flex flex-wrap gap-2">
					<Button type="button" variant="outline" size="sm" onClick={() => setDomains([...domains, { domain: "" }])}>
						<Plus className="mr-1 h-4 w-4" /> Add domain
					</Button>
					<Button
						type="button"
						variant="link"
						size="sm"
						onClick={() => onInsertExample(EXAMPLE(hostnameOf(domains[0]?.domain || "shop.com")))}
					>
						Insert example
					</Button>
				</div>

				{insecure.length > 0 && (
					<p className="text-xs text-amber-600 dark:text-amber-400">
						Warning: {insecure.map((d) => hostnameOf(d.domain)).join(", ")} is used over http://, so the secret
						and customer data are sent unencrypted. Prefer https://.
					</p>
				)}
			</div>
		</details>
	);
}
