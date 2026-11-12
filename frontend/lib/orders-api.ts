import { API_BASE_URL, apiFetch } from "@/lib/api";

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
	live_lookup: LiveLookup;
	draft_expiry_hours: number;
	enabled: boolean;
	test_mode: boolean;
	settings_confirmed_at: string | null;
	currency: string;
	prompt_preview: string;
};

export type LiveHeader = { name: string; value?: string; has_value?: boolean };
export type LiveLookup = {
	enabled: boolean;
	url_template: string;
	price_path: string;
	available_path: string | null;
	headers: LiveHeader[];
};

export type SetupStepStatus = "pending" | "running" | "done" | "failed" | "needs_input";
export type SetupStep = {
	key: "content" | "products" | "delivery";
	label: string;
	status: SetupStepStatus;
	detail: string;
	error_code: string;
	error: string;
};
export type SetupState = {
	status: "not_started" | "running" | "ready" | "failed" | "needs_input";
	steps: SetupStep[];
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
	runSetup: (projectId: string, kbId: string, mode: "resume" | "restart" = "resume") =>
		apiFetch<{ status: string }>("/api/orders/setup/run/", { method: "POST", json: { project_id: projectId, kb_id: kbId, mode } }),
	setupState: (projectId: string) => apiFetch<SetupState>(`/api/orders/setup/${withProject(projectId)}`),
	list: (projectId: string, filters: OrderFilters, page = 1) => {
		const params = new URLSearchParams({ project_id: projectId, page: String(page) });
		Object.entries(filters).forEach(([key, value]) => {
			if (value) params.append(key, value);
		});
		return apiFetch<{ orders: OrderRow[]; count: number }>(`/api/orders/?${params.toString()}`);
	},
	detail: (id: string) => apiFetch<OrderDetail>(`/api/orders/${id}/`),
	patch: (id: string, data: Record<string, unknown>) =>
		apiFetch<OrderDetail>(`/api/orders/${id}/`, { method: "PATCH", json: data }),
	bulkStatus: (ids: string[], status: string) =>
		apiFetch<{ results: { id: string; ok: boolean; error?: string }[] }>("/api/orders/bulk-status/", {
			method: "POST",
			json: { ids, status },
		}),
	exportUrl: (projectId: string, filters: OrderFilters) => {
		const params = new URLSearchParams({ project_id: projectId });
		Object.entries(filters).forEach(([key, value]) => {
			if (value) params.append(key, value);
		});
		return `${API_BASE_URL}/api/orders/export.csv?${params.toString()}`;
	},
};

export type OrderFilters = {
	status?: string;
	channel?: string;
	date_from?: string;
	date_to?: string;
	is_test?: string;
	q?: string;
	conversation_id?: string;
};

export type ActionHeader = { name: string; value?: string; has_value?: boolean };
export type OrderAction = {
	id: number;
	name: string;
	method: "GET" | "POST";
	url: string;
	headers: ActionHeader[];
	body_template: string;
	response_mapping: { tracking_number?: string };
	success_status: string;
	success_codes: number[];
	auto_run: boolean;
	is_active: boolean;
};
export type ActionRun = {
	id: number;
	action_id: number;
	action_name: string;
	order_id: string;
	status: "queued" | "running" | "succeeded" | "failed";
	attempt: number;
	http_status: number | null;
	error: string;
	response_excerpt: string;
	created_at: string;
	finished_at: string | null;
};
export type ActionProgress = { counts: Record<ActionRun["status"], number>; total: number };

export const actionApi = {
	list: (projectId: string) => apiFetch<OrderAction[]>(`/api/orders/actions/${withProject(projectId)}`),
	create: (projectId: string, data: Partial<OrderAction>) =>
		apiFetch<OrderAction>(`/api/orders/actions/${withProject(projectId)}`, { method: "POST", json: data }),
	update: (id: number, projectId: string, data: Partial<OrderAction>) =>
		apiFetch<OrderAction>(`/api/orders/actions/${id}/${withProject(projectId)}`, { method: "PATCH", json: data }),
	remove: (id: number, projectId: string) =>
		apiFetch<{ deleted: boolean }>(`/api/orders/actions/${id}/${withProject(projectId)}`, { method: "DELETE" }),
	run: (id: number, projectId: string, orderIds: string[], force = false) =>
		apiFetch<{ queued: number[]; already_sent: number[] }>(`/api/orders/actions/${id}/run/${withProject(projectId)}`, {
			method: "POST",
			json: { order_ids: orderIds, force },
		}),
	progress: (id: number, projectId: string, orderIds: string[]) =>
		apiFetch<ActionProgress>(
			`/api/orders/actions/${id}/progress/${withProject(projectId)}&order_ids=${orderIds.join(",")}`,
		),
	test: (id: number, projectId: string, orderNumber: number, force = false) =>
		apiFetch<{ ok: boolean; http_status: number | null; error: string; response_excerpt: string }>(
			`/api/orders/actions/${id}/test/${withProject(projectId)}`,
			{ method: "POST", json: { order_number: orderNumber, force } },
		),
	runs: (orderId: string) => apiFetch<ActionRun[]>(`/api/orders/actions/runs/?order_id=${orderId}`),
	retry: (runId: number) => apiFetch<ActionRun>(`/api/orders/actions/runs/${runId}/retry/`, { method: "POST" }),
};

export type OrderRow = {
	id: string;
	number: number | null;
	status: string;
	channel: string;
	is_test: boolean;
	customer_name: string;
	customer_phone: string;
	zone_name: string;
	currency: string;
	total: string;
	collect_amount: string;
	conversation_id: string | null;
	created_at: string;
};

export type OrderItemRow = {
	id: string;
	product_id: string | null;
	variant_id: string | null;
	name: string;
	sku: string;
	variant_label: string;
	unit_price: string;
	quantity: number;
	line_total: string;
};

export type OrderDetail = OrderRow & {
	customer_address: string;
	customer_note: string;
	custom: Record<string, string>;
	subtotal: string;
	zone_charge: string;
	advance_paid: string;
	tracking_number: string;
	internal_note: string;
	submitted_at: string | null;
	items: OrderItemRow[];
	history: { from_status: string; to_status: string; actor_type: string; actor_id: string; created_at: string }[];
};

export type OrderInstruction = {
	id: string;
	label: string;
	message: string;
	scope?: "general" | "order" | "off";
	knowledge_base_id: string | null;
	created_at: string;
	updated_at: string;
};

export type PrefillLine = { product_id: string; name: string; variant_id: string | null; quantity: number };

export type Prefill = {
	items: PrefillLine[];
	customer: Partial<Record<"customer_name" | "customer_phone" | "customer_address" | "zone_name" | "customer_note", string>>;
	needs_price: { product_id: string; name: string }[];
};

export const orderFromChatApi = {
	prefill: (conversationId: string) =>
		apiFetch<Prefill>("/api/orders/from-chat/", { method: "POST", json: { conversation_id: conversationId } }),
	create: (
		conversationId: string,
		items: { product_id: string; variant_id: string | null; quantity: number }[],
		customer: Record<string, string>
	) =>
		apiFetch<{ order_number: number | null; status: string; problems?: { message: string }[] }>("/api/orders/", {
			method: "POST",
			json: { conversation_id: conversationId, items, customer },
		}),
};

export const orderInstructionApi = {
	list: (projectId: string) =>
		apiFetch<{ instructions: OrderInstruction[]; count: number }>(`/api/kb/instructions/${withProject(projectId)}`),
	create: (projectId: string, data: { label: string; message: string; scope: "order" }) =>
		apiFetch<{ instruction: OrderInstruction; warnings: string[] }>("/api/kb/instructions/", {
			method: "POST",
			json: { project_id: projectId, ...data },
		}),
	update: (id: string, data: { label?: string; message?: string; scope?: "order" | "off" }) =>
		apiFetch<{ instruction: OrderInstruction; warnings: string[] }>(`/api/kb/instructions/${id}/`, {
			method: "PATCH",
			json: data,
		}),
	improve: (projectId: string, text: string, scope: "general" | "order" = "order") =>
		apiFetch<{ suggestion: string; why: string }>("/api/kb/instructions/improve/", {
			method: "POST",
			json: { project_id: projectId, text, scope },
		}),
};
