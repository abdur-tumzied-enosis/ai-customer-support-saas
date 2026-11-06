import { apiFetch } from "@/lib/api";

export type Requirement = {
	key: string;
	label: string;
	status: "ok" | "fail" | "warn";
	hard: boolean;
	setup: string;
};

export type OrderZone = { name: string; charge: string | null };
export type PaymentMethod = { label: string; default: boolean };
export type CustomField = { key: string; label: string; required: boolean };

export type OrderSettings = {
	channels: string[];
	zones: OrderZone[];
	payment_methods: PaymentMethod[];
	form_fields: { optional: string[]; custom: CustomField[] };
	notification_emails: string[];
	draft_expiry_hours: number;
	enabled: boolean;
	test_mode: boolean;
	currency: string;
	prompt_preview: string;
};

export type SetupState = {
	status: "not_started" | "running" | "ready";
	report: { found?: Record<string, number>; not_found?: string[] };
};

const withProject = (projectId: string) => `?project_id=${encodeURIComponent(projectId)}`;

export const orderApi = {
	requirements: (projectId: string) => apiFetch<Requirement[]>(`/api/orders/requirements/${withProject(projectId)}`),
	enable: (projectId: string) => apiFetch<{ enabled: boolean }>(`/api/orders/enable/${withProject(projectId)}`, { method: "POST" }),
	disable: (projectId: string) =>
		apiFetch<{ enabled: boolean; test_mode: boolean }>(`/api/orders/disable/${withProject(projectId)}`, { method: "POST" }),
	setTestMode: (projectId: string, on: boolean) =>
		apiFetch<{ test_mode: boolean }>(`/api/orders/test-mode/${withProject(projectId)}`, { method: "POST", json: { on } }),
	settings: (projectId: string) => apiFetch<OrderSettings>(`/api/orders/settings/${withProject(projectId)}`),
	saveSettings: (projectId: string, data: Partial<OrderSettings>) =>
		apiFetch<OrderSettings>(`/api/orders/settings/${withProject(projectId)}`, { method: "PATCH", json: data }),
	runSetup: (projectId: string, kbId: string) =>
		apiFetch<{ status: string }>("/api/orders/setup/run/", { method: "POST", json: { project_id: projectId, kb_id: kbId } }),
	setupState: (projectId: string) => apiFetch<SetupState>(`/api/orders/setup/${withProject(projectId)}`),
};
