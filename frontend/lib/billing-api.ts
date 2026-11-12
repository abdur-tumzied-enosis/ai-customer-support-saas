import { apiFetch } from "@/lib/api";

export type Wallet = { balance: string; low: boolean; empty: boolean };
export type CreditTxn = {
	id: number;
	created_at: string;
	kind: "grant" | "adjust" | "charge" | "initial_allocation" | "signup_bonus";
	action: string;
	action_label: string;
	amount: string;
	balance_after: string;
	note: string;
	conversation_id: string | null;
};
export type PublicPricing = {
	packages: { id: number; name: string; credits: number; bonus_credits: number; price: string; currency: string; features: string[]; highlighted: boolean }[];
	actions: { action: string; label: string; credits: string }[];
	signup_credits: string;
};

export type CreditRequest = { id: number; reference: string; status: string; package: string; amount: string; currency: string; bkash_number: string };

export const billingApi = {
	requestCredits: (projectId: string, packageId: number, phone: string) =>
		apiFetch<CreditRequest>("/api/billing/credit-requests/", { method: "POST", json: { project_id: projectId, package_id: packageId, phone } }).then((r) => r.data),
	submitPayment: (projectId: string, id: number, trxId: string, bkashPhone: string) =>
		apiFetch<CreditRequest>(`/api/billing/credit-requests/${id}/payment/`, { method: "POST", json: { project_id: projectId, trx_id: trxId, bkash_phone: bkashPhone } }).then((r) => r.data),
	wallet: (projectId: string) => apiFetch<Wallet>(`/api/billing/wallet/?project_id=${projectId}`).then((r) => r.data),
	transactions: (projectId: string, p: { kind?: string; offset?: number; limit?: number }) => {
		const q = new URLSearchParams({ project_id: projectId });
		if (p.kind) q.set("kind", p.kind);
		if (p.offset) q.set("offset", String(p.offset));
		if (p.limit) q.set("limit", String(p.limit));
		return apiFetch<{ results: CreditTxn[]; count: number }>(`/api/billing/transactions/?${q}`).then((r) => r.data);
	},
	pricing: () => apiFetch<PublicPricing>("/api/billing/pricing/").then((r) => r.data),
};

// Whole numbers without decimals; up to 2 decimals only when the value has them. Minus sign for negatives.
export function formatCredits(value: string | number): string {
	const n = Number(value);
	if (!Number.isFinite(n)) return "0";
	return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
}
