import { downscaleImage, MAX_PHOTO_BYTES, putToR2, sha256Hex, type PresignedUpload } from "./upload";

export const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8251";

type FetchOptions = RequestInit & { json?: unknown };

export type ApiSuccessResponse<T> = {
	success: true;
	message: string;
	data: T;
};

export type ApiErrorResponse = {
	success: false;
	error: {
		code: string;
		message: string;
		details?: Record<string, string> | string | string[] | null;
	};
};

export type ApiResponse<T> = ApiSuccessResponse<T> | ApiErrorResponse;

export class ApiError extends Error {
	code: string;
	details?: Record<string, string> | string | string[] | null;

	constructor(error: ApiErrorResponse["error"]) {
		super(error.message);
		this.name = "ApiError";
		this.code = error.code;
		this.details = error.details;
	}
}

/**
 * Get CSRF token from cookies
 */
function getCsrfToken(): string | null {
	if (typeof document === "undefined") return null;
	
	const cookies = document.cookie.split(";");
	for (const cookie of cookies) {
		const [name, value] = cookie.trim().split("=");
		if (name === "csrftoken") {
			return decodeURIComponent(value);
		}
	}
	return null;
}

export async function apiFetch<T>(path: string, options: FetchOptions = {}): Promise<ApiSuccessResponse<T>> {
	const headers: HeadersInit = {
		"Content-Type": "application/json",
		...(options.headers || {}),
	};

	// Add CSRF token for state-changing requests (POST, PUT, DELETE, PATCH)
	const method = options.method?.toUpperCase() || "GET";
	if (["POST", "PUT", "DELETE", "PATCH"].includes(method)) {
		const csrfToken = getCsrfToken();
		if (csrfToken) {
			(headers as Record<string, string>)["X-CSRFToken"] = csrfToken;
		}
	}

	const credentials: RequestCredentials = "include"; // Include cookies for session auth

	const res = await fetch(`${API_BASE_URL}${path}`, {
		...options,
		headers,
		credentials,
		body: options.json !== undefined ? JSON.stringify(options.json) : options.body,
	});

	const data = (await res.json()) as ApiResponse<T>;

	if (!res.ok || !data.success) {
		if (!data.success) {
			throw new ApiError(data.error);
		}
		throw new ApiError({
			code: "request_failed",
			message: `Request failed with status ${res.status}`,
		});
	}

	return data;
}

// Auth API functions
export const authApi = {
	signup: async (data: {
		first_name: string;
		last_name: string;
		business_name?: string;
		email: string;
		password: string;
		terms_version: string;
	}) => {
		return apiFetch<{
			user: {
				id: number;
				username: string;
				email: string;
				first_name: string;
				last_name: string;
			};
			business?: {
				id: number;
				name: string;
				slug: string;
			};
		}>("/api/auth/signup/", {
			method: "POST",
			json: {
				first_name: data.first_name,
				last_name: data.last_name,
				business_name: data.business_name || "",
				email: data.email,
				password: data.password,
				terms_version: data.terms_version,
			},
		});
	},

	login: async (data: { email: string; password: string }) => {
		return apiFetch<{
			user: {
				id: number;
				username: string;
				email: string;
				first_name: string;
				last_name: string;
			};
			projects: Array<{
				id: number;
				name: string;
				slug: string;
				role: string;
			}>;
		}>("/api/auth/login/", {
			method: "POST",
			json: {
				email: data.email,
				password: data.password,
			},
		});
	},

	logout: async () => {
		return apiFetch("/api/auth/logout/", {
			method: "POST",
		});
	},

	getCurrentUser: async () => {
		return apiFetch<{
			user: {
				id: number;
				username: string;
				email: string;
				first_name: string;
				last_name: string;
			};
			projects: Array<{
				id: number;
				name: string;
				slug: string;
				role: string;
			}>;
		}>("/api/auth/me/");
	},

	updateProfile: async (data: { first_name: string; last_name: string }) =>
		apiFetch("/api/auth/profile/", { method: "PATCH", json: data }),

	changePassword: async (data: { current_password: string; new_password: string }) =>
		apiFetch("/api/auth/password/", { method: "POST", json: data }),

	// Signs the user in on success (same payload as login)
	verifyEmail: async (data: { email: string; code: string }) =>
		apiFetch("/api/auth/verify-email/", { method: "POST", json: data }),

	resendVerification: async (email: string) =>
		apiFetch("/api/auth/verify-email/resend/", { method: "POST", json: { email } }),

	requestPasswordReset: async (email: string) =>
		apiFetch("/api/auth/password-reset/", { method: "POST", json: { email } }),

	confirmPasswordReset: async (data: { uid: string; token: string; new_password: string }) =>
		apiFetch("/api/auth/password-reset/confirm/", { method: "POST", json: data }),

	deleteAccount: async (password: string) => apiFetch("/api/auth/delete/", { method: "POST", json: { password } }),

	// Top-level navigation: the session cookie goes along and Content-Disposition makes it a download
	exportUrl: `${API_BASE_URL}/api/auth/export/`,
};

// Document API functions
export const documentApi = {
	// Presign (duplicate check on the hash) -> PUT to R2 (R2 enforces the signed hash) -> confirm (starts ingestion)
	upload: async (file: File, projectId: string, kbId: string) => {
		const presigned = await apiFetch<PresignedUpload & { document_id: string }>("/api/kb/documents/upload/", {
			method: "POST",
			json: {
				project_id: projectId,
				kb_id: kbId,
				filename: file.name,
				size: file.size,
				content_type: file.type,
				sha256: await sha256Hex(file),
			},
		});
		await putToR2(presigned.data, file);
		return apiFetch<{
			document: {
				id: string;
				filename: string;
				file_size: number;
				status: string;
				created_at: string;
			};
			kb: {
				id: string;
				name: string;
				project_id: string;
				project_name: string;
			};
			ingestion_job: {
				id: string;
				status: string;
			};
		}>(`/api/kb/documents/${presigned.data.document_id}/confirm/`, { method: "POST" });
	},

	list: async (projectId?: string, kbId?: string, status?: string) => {
		const params = new URLSearchParams();
		if (projectId) params.append("project_id", projectId);
		if (kbId) params.append("kb_id", kbId);
		if (status) params.append("status", status);

		const queryString = params.toString();
		const path = `/api/kb/documents/${queryString ? `?${queryString}` : ""}`;

		return apiFetch<{
			documents: Array<{
				id: string;
				filename: string;
				file_size: number;
				status: string;
				error_message: string | null;
				checksum: string;
				created_at: string;
				source_type: string;
				page_url: string;
				page_title: string;
				crawl_depth: number;
				parent_crawl_id: string | null;
				children_count: number;
				ingestion_job: {
					id: string | null;
					status: string | null;
					progress: number;
					error: string | null;
					created_at: string | null;
					completed_at: string | null;
				} | null;
			}>;
			count: number;
		}>(path);
	},

	delete: async (documentId: string) => {
		return apiFetch(`/api/kb/documents/${documentId}/`, {
			method: "DELETE",
		});
	},

	crawl: async (url: string, depth: number, projectId: string, kbId: string, searchQuery?: string) => {
		return apiFetch<{
			document: {
				id: string;
				uri: string;
				page_url: string;
				search_query: string;
				status: string;
			};
			kb: {
				id: string;
				name: string;
				project_id: string;
			};
			ingestion_job: {
				id: string;
				status: string;
			};
		}>("/api/kb/crawl/", {
			method: "POST",
			json: {
				url,
				depth,
				project_id: projectId,
				kb_id: kbId,
				...(searchQuery ? { search_query: searchQuery } : {}),
			},
		});
	},

	getMarkdown: async (documentId: string) => {
		return apiFetch<{
			document: {
				id: string;
				page_url: string;
				page_title: string;
				markdown_content: string;
				images_metadata: Array<{
					src: string;
					alt?: string;
					title?: string;
				}>;
				created_at: string;
			};
		}>(`/api/kb/documents/${documentId}/markdown/`);
	},
};

export type ApiDomain = {
	domain: string;
	auth_header?: string;
	has_secret?: boolean; // from server only
	secret?: string; // to server only; blank keeps the stored secret
};

// Custom Instruction type
export type CustomInstruction = {
	scope?: "general" | "order" | "off";
	id: string;
	label: string;
	message: string;
	knowledge_base_id: string | null;
	knowledge_base_name: string | null;
	api_tool_enabled: boolean;
	api_domains: ApiDomain[];
	created_at: string;
	updated_at: string;
};

// Custom Instruction API functions
export const customInstructionApi = {
	/**
	 * List instructions for a project.
	 * @param projectId - Project UUID
	 * @param knowledgeBaseId - Optional KB UUID to filter by specific KB (null for project-level only)
	 */
	list: async (projectId: string, knowledgeBaseId?: string | null) => {
		const params = new URLSearchParams();
		params.append("project_id", projectId);
		if (knowledgeBaseId !== undefined) {
			params.append("knowledge_base_id", knowledgeBaseId || "");
		}

		return apiFetch<{
			instructions: CustomInstruction[];
			count: number;
		}>(`/api/kb/instructions/?${params.toString()}`);
	},

	/**
	 * What the bot follows for a KB: shared instructions, then the KB's own (on top).
	 */
	resolve: async (projectId: string, knowledgeBaseId?: string) => {
		const params = new URLSearchParams();
		params.append("project_id", projectId);
		if (knowledgeBaseId) {
			params.append("knowledge_base_id", knowledgeBaseId);
		}

		return apiFetch<{
			instructions: CustomInstruction[];
			count: number;
			source: "project" | "knowledge_base";
		}>(`/api/kb/instructions/resolve/?${params.toString()}`);
	},

	/**
	 * Create a new instruction.
	 * @param data.project_id - Project UUID (required)
	 * @param data.knowledge_base_id - KB UUID (optional, null for project-level)
	 * @param data.label - Instruction label
	 * @param data.message - Instruction message
	 */
	create: async (data: {
		project_id: string;
		knowledge_base_id?: string | null;
		label: string;
		message: string;
		api_tool_enabled?: boolean;
		api_domains?: ApiDomain[];
	}) => {
		return apiFetch<{
			instruction: CustomInstruction;
		}>("/api/kb/instructions/", {
			method: "POST",
			json: data,
		});
	},

	/**
	 * Get a specific instruction.
	 */
	get: async (instructionId: string) => {
		return apiFetch<{
			instruction: CustomInstruction;
		}>(`/api/kb/instructions/${instructionId}/`);
	},

	/**
	 * Update an instruction.
	 */
	update: async (
		instructionId: string,
		data: { label?: string; message?: string; api_tool_enabled?: boolean; api_domains?: ApiDomain[] }
	) => {
		return apiFetch<{
			instruction: CustomInstruction;
		}>(`/api/kb/instructions/${instructionId}/`, {
			method: "PATCH",
			json: data,
		});
	},

	/**
	 * Delete an instruction.
	 */
	delete: async (instructionId: string) => {
		return apiFetch(`/api/kb/instructions/${instructionId}/`, {
			method: "DELETE",
		});
	},
};

export type KnowledgeBase = {
	id: string;
	name: string;
	is_default: boolean;
	document_count: number;
	created_at: string;
};

// Agent Memory API functions
export const knowledgeBaseApi = {
	list: async (projectId: string) =>
		apiFetch<{ knowledge_bases: KnowledgeBase[]; count: number }>(`/api/kb/knowledge-bases/?project_id=${projectId}`),

	create: async (projectId: string, name: string) =>
		apiFetch<{ knowledge_base: KnowledgeBase }>("/api/kb/knowledge-bases/", {
			method: "POST",
			json: { project_id: projectId, name },
		}),

	rename: async (kbId: string, name: string) =>
		apiFetch<{ knowledge_base: KnowledgeBase }>(`/api/kb/knowledge-bases/${kbId}/`, {
			method: "PATCH",
			json: { name },
		}),
};

// Instruction Template types and API
export type InstructionTemplate = {
	id: string;
	label: string;
	message: string;
	description: string;
	category: string;
	usage_count: number;
};

export const instructionTemplateApi = {
	list: async (category?: string) => {
		const params = category ? `?category=${encodeURIComponent(category)}` : '';
		return apiFetch<{
			templates: InstructionTemplate[];
			count: number;
		}>(`/api/kb/instruction-templates/${params}`);
	},

	recordUse: async (templateId: string) => {
		return apiFetch(`/api/kb/instruction-templates/${templateId}/use/`, {
			method: "POST",
		});
	},
};

// Platform types
export type CredentialField = {
	name: string;
	label: string;
	type: string;
	required: boolean;
	description: string;
	placeholder: string;
};

export type Platform = {
	id: string;
	name: string;
	display_name: string;
	description: string;
	icon: string;
	is_active: boolean;
	webhook_path: string;
	webhook_url: string;
	api_base_url: string;
	documentation_url: string;
	credential_schema: CredentialField[];
};

// Channel Credential types
export type ChannelCredential = {
	id: string;
	name: string;
	channel: string;
	platform: {
		id: string;
		name: string;
		display_name: string;
		icon: string;
	} | null;
	knowledge_base: {
		id: string;
		name: string;
	} | null;
	credentials?: Record<string, string>;
	is_active: boolean;
	image_search_enabled: boolean;
	is_configured: boolean;
	created_at: string;
	updated_at: string;
};

// Platform API functions
export const platformApi = {
	list: async () => {
		return apiFetch<{
			platforms: Platform[];
			count: number;
		}>("/api/channels/platforms");
	},
};

// Channel API functions
export const channelApi = {
	list: async (projectSlug: string) => {
		return apiFetch<{
			channels: ChannelCredential[];
			count: number;
		}>(`/api/channels/channels?project_slug=${encodeURIComponent(projectSlug)}`);
	},

	create: async (data: {
		project_slug: string;
		name: string;
		channel: string;
		credentials: Record<string, string>;
		knowledge_base_id?: string | null;
		is_active?: boolean;
	}) => {
		return apiFetch<{
			channel: ChannelCredential;
		}>("/api/channels/channels", {
			method: "POST",
			json: data,
		});
	},

	get: async (channelId: string) => {
		return apiFetch<{
			channel: ChannelCredential;
		}>(`/api/channels/channels/${channelId}`);
	},

	update: async (channelId: string, data: {
		name?: string;
		credentials?: Record<string, string>;
		knowledge_base_id?: string | null;
		is_active?: boolean;
		image_search_enabled?: boolean;
	}) => {
		return apiFetch<{
			channel: ChannelCredential;
		}>(`/api/channels/channels/${channelId}`, {
			method: "PATCH",
			json: data,
		});
	},

	delete: async (channelId: string) => {
		return apiFetch(`/api/channels/channels/${channelId}`, {
			method: "DELETE",
		});
	},

	// Legacy endpoints
	configureFacebook: async (data: {
		project_slug: string;
		page_id?: string;
		page_access_token?: string;
		meta_app_id?: string;
		meta_app_secret?: string;
		verify_token?: string;
	}) => {
		return apiFetch<{
			status: string;
			project: string;
		}>("/api/channels/facebook/config", {
			method: "POST",
			json: data,
		});
	},

	getCredentials: async (projectSlug: string) => {
		return apiFetch<{
			project: string;
			channels: Array<{
				id: string;
				channel: string;
				is_configured: boolean;
			}>;
		}>(`/api/channels/credentials?project_slug=${encodeURIComponent(projectSlug)}`);
	},
};

export type Conversation = {
	id: string;
	name: string;
	knowledge_base_id: string;
	knowledge_base_name: string;
	channel: string; // "self" (test/organic) | "facebook" | ...
	contact_display_name: string;
	handoff_at: string | null;
	handoff_reason: string;
	is_paused: boolean;
	resumes_at: string | null;
	created_at: string;
	updated_at: string;
	credits_used?: string; // detail endpoint only
};

export type ConversationListItem = Conversation & {
	message_count: number;
	credits_used: string;
	last_message: { id: string; text: string; created_at: string } | null;
};

export type ConversationMessage = {
	id: string;
	direction: "in" | "out";
	sender: "customer" | "bot" | "staff";
	status: "pending" | "processing" | "completed" | "failed";
	text: string;
	created_at: string;
	agent_reply_id: string | null;
	has_photo?: boolean;
	photo_matches?: string;
	credits?: { action: string; label: string; credits: string }[];
};
// Conversation API functions
export const conversationApi = {
	list: async (projectSlug: string, opts: { needsHuman?: boolean } = {}) => {
		const params = new URLSearchParams({ project_slug: projectSlug });
		if (opts.needsHuman) params.append("needs_human", "1");
		return apiFetch<{ conversations: ConversationListItem[]; needs_human_count: number }>(
			`/api/channels/conversations?${params.toString()}`
		);
	},

	get: async (conversationId: string) => {
		return apiFetch<{ conversation: Conversation }>(`/api/channels/conversations/${conversationId}`);
	},

	create: async (data: {
		project_slug: string;
		name?: string;
		knowledge_base_id?: string;
	}) => {
		return apiFetch<{ conversation: Conversation }>("/api/channels/conversations", {
			method: "POST",
			json: data,
		});
	},

	update: async (conversationId: string, data: { name?: string }) => {
		return apiFetch<{ conversation: Conversation }>(`/api/channels/conversations/${conversationId}`, {
			method: "PATCH",
			json: data,
		});
	},

	delete: async (conversationId: string) => {
		return apiFetch(`/api/channels/conversations/${conversationId}`, {
			method: "DELETE",
		});
	},

	getMessages: async (conversationId: string, limit: number = 20, offset?: number) => {
		const params = new URLSearchParams();
		params.append("limit", limit.toString());
		if (offset !== undefined) {
			params.append("offset", offset.toString());
		}
		
		return apiFetch<{
			messages: ConversationMessage[];
			pagination: {
				count: number;
				limit: number;
				offset: number;
				has_next: boolean;
				has_previous: boolean;
				next_offset: number | null;
				previous_offset: number | null;
			};
		}>(`/api/channels/conversations/${conversationId}/messages?${params.toString()}`);
	},

	sendMessage: async (conversationId: string, text: string) => {
		return apiFetch<{ message: ConversationMessage }>(`/api/channels/conversations/${conversationId}/messages`, {
			method: "POST",
			json: { text },
		});
	},

	sendPhoto: async (conversationId: string, file: File, text?: string) => {
		const photo = await downscaleImage(file);
		const base = `/api/channels/conversations/${conversationId}/photo`;
		const presigned = await apiFetch<PresignedUpload & { key: string }>(`${base}/presign`, {
			method: "POST",
			json: { content_type: photo.type, size: photo.size },
		});
		await putToR2(presigned.data, photo);
		return apiFetch<{ message: ConversationMessage }>(base, { method: "POST", json: { key: presigned.data.key, text } });
	},

	getMessageStatus: async (messageId: string) => {
		return apiFetch<{
			message: {
				id: string;
				status: "pending" | "processing" | "completed" | "failed";
				text: string;
				created_at: string;
				agent_reply_id: string | null;
				agent_reply: {
					id: string;
					text: string;
					created_at: string;
				} | null;
			};
		}>(`/api/channels/messages/${messageId}/status`);
	},

	handoff: async (conversationId: string, reason?: string) =>
		apiFetch<{ conversation: Conversation }>(`/api/channels/conversations/${conversationId}/handoff`, {
			method: "POST",
			json: reason ? { reason } : {},
		}),

	resume: async (conversationId: string) =>
		apiFetch<{ conversation: Conversation }>(`/api/channels/conversations/${conversationId}/resume`, {
			method: "POST",
			json: {},
		}),

	staffReply: async (conversationId: string, text: string) =>
		apiFetch<{ message: ConversationMessage }>(`/api/channels/conversations/${conversationId}/staff-reply`, {
			method: "POST",
			json: { text },
		}),

	getMemory: async (conversationId: string) =>
		apiFetch<ConversationMemory>(`/api/channels/conversations/${conversationId}/memory`),

	// key omitted: forget everything
	forgetMemory: async (conversationId: string, key?: string) =>
		apiFetch<ConversationMemory>(
			`/api/channels/conversations/${conversationId}/memory${key ? `?key=${encodeURIComponent(key)}` : ""}`,
			{ method: "DELETE" }
		),
};

// Agent-drafted knowledge (from staff answers) and instructions (from handoffs), approved by staff
export type MemorySuggestion = {
	id: string;
	kind: "knowledge" | "instruction";
	title: string;
	body: string;
	rationale: string;
	status: "pending" | "approved" | "rejected";
	knowledge_base_id: string;
	source_conversation_id: string | null;
	created_at: string;
};

export const suggestionApi = {
	list: async (projectId: string) =>
		apiFetch<{ suggestions: MemorySuggestion[] }>(`/api/kb/suggestions/?project_id=${encodeURIComponent(projectId)}`),

	approve: async (id: string, edits: { title: string; body: string }) =>
		apiFetch<{ suggestion: MemorySuggestion }>(`/api/kb/suggestions/${id}/approve/`, { method: "POST", json: edits }),

	reject: async (id: string) =>
		apiFetch<{ suggestion: MemorySuggestion }>(`/api/kb/suggestions/${id}/reject/`, { method: "POST", json: {} }),
};

export type MemoryFact ={ key: string; value: string; source_msg_id: string; updated_at: string };
export type MemoryEpisode = { id: string; started_at: string; ended_at: string; summary: string };
export type ConversationMemory = { facts: MemoryFact[]; episodes: MemoryEpisode[] };

// Analytics types
export type AnalyticsData = {
	date_range: {
		start: string;
		end: string;
	};
	conversations: {
		total: number;
		trend: Array<{
			date: string;
			count: number;
		}>;
	};
	messages: {
		total: number;
		inbound: number;
		outbound: number;
		by_status: Record<string, number>;
		by_platform: Array<{
			contact__platform: string;
			count: number;
		}>;
		trend: Array<{
			date: string;
			count: number;
		}>;
	};
	knowledge: {
		total_documents: number;
		documents_by_status: Record<string, number>;
		total_storage_bytes: number;
		knowledge_bases: number;
		ingestion_jobs: {
			total: number;
			by_status: Record<string, number>;
		};
	};
	channels: {
		total: number;
		by_type: Record<string, number>;
	};
	users: {
		active: number;
		new_signups: number;
		trend: Array<{
			date: string;
			count: number;
		}>;
	};
	photos: {
		searches: number;
	};
};

// Analytics API functions
export const analyticsApi = {
	/**
	 * Get analytics overview data
	 * @param startDate - Optional start date (ISO format)
	 * @param endDate - Optional end date (ISO format)
	 */
	getOverview: async (startDate?: string, endDate?: string) => {
		const params = new URLSearchParams();
		if (startDate) params.append("start_date", startDate);
		if (endDate) params.append("end_date", endDate);

		const queryString = params.toString();
		const path = `/api/analytics/overview/${queryString ? `?${queryString}` : ""}`;

		return apiFetch<AnalyticsData>(path);
	},
};
export type ProjectWebhook = { url: string; is_active: boolean; secret: string };

export const webhookApi = {
	get: async (projectSlug: string) =>
		apiFetch<{ webhook: ProjectWebhook }>(`/api/channels/webhook?project_slug=${encodeURIComponent(projectSlug)}`),

	update: async (projectSlug: string, data: { url: string; is_active: boolean }) =>
		apiFetch<{ webhook: ProjectWebhook }>("/api/channels/webhook", {
			method: "PUT",
			json: { project_slug: projectSlug, ...data },
		}),

	rotate: async (projectSlug: string) =>
		apiFetch<{ webhook: ProjectWebhook }>("/api/channels/webhook/rotate-secret", {
			method: "POST",
			json: { project_slug: projectSlug },
		}),

	test: async (projectSlug: string) =>
		apiFetch<{ ok: boolean }>("/api/channels/webhook/test", {
			method: "POST",
			json: { project_slug: projectSlug },
		}),
};

export type WidgetAppearance = {
	title: string;
	welcome_message: string;
	primary_color: string;
	position: "left" | "right";
	privacy_url: string;
	show_branding: boolean;
};

export type Widget = {
	id: string;
	name: string;
	public_key: string;
	knowledge_base: { id: string; name: string };
	is_active: boolean;
	image_search_enabled: boolean;
	allowed_origins: string[];
	appearance: WidgetAppearance;
	snippet: string;
	created_at: string;
	updated_at: string;
};

export type WidgetInput = Partial<{
	name: string;
	knowledge_base_id: string;
	is_active: boolean;
	image_search_enabled: boolean;
	allowed_origins: string[];
	appearance: Partial<WidgetAppearance>;
}>;

export const widgetApi = {
	list: async (projectSlug: string) =>
		apiFetch<{ widgets: Widget[] }>(`/api/channels/widgets?project_slug=${encodeURIComponent(projectSlug)}`),

	create: async (projectSlug: string, data: WidgetInput) =>
		apiFetch<{ widget: Widget }>("/api/channels/widgets", { method: "POST", json: { project_slug: projectSlug, ...data } }),

	update: async (id: string, data: WidgetInput) =>
		apiFetch<{ widget: Widget }>(`/api/channels/widgets/${id}`, { method: "PATCH", json: data }),

	remove: async (id: string) => apiFetch<Record<string, never>>(`/api/channels/widgets/${id}`, { method: "DELETE" }),
};

export type ProductImage = {
	id: string;
	status: "uploading" | "pending" | "ready" | "failed";
	url: string;
	thumb_url: string;
};

export type ProductStatus = "needs_info" | "active" | "hidden";

export type ProductVariant = {
	id?: string;
	external_id: string | null;
	sku: string;
	label: string;
	attributes: Record<string, string>;
	price: string | null;
	available: boolean;
};

export type Product = {
	id: string;
	kb_id: string;
	external_id: string | null;
	sku: string;
	name: string;
	description: string;
	price: string | null;
	currency: string;
	available: boolean;
	status: ProductStatus;
	product_url: string;
	source: string;
	custom: Record<string, unknown>;
	variants: ProductVariant[];
	created_at: string;
	images: ProductImage[];
};

export type ProductField = {
	id: string;
	kb_id: string;
	key: string;
	label: string;
	kind: string;
	choices: string[];
	required: boolean;
	show_to_agent: boolean;
	position: number;
};

export type ProductPatch = Partial<Pick<Product, "name" | "description" | "sku" | "external_id" | "price" | "available" | "custom" | "product_url">> & {
	status?: ProductStatus;
	variants?: Omit<ProductVariant, "id">[];
};

export type Features = { image_search_available: boolean; image_search: boolean };

type ProductUploads = { product: Product; uploads: (PresignedUpload & { image_id: string })[] };

// Downscale -> presign (`path` creates the product or adds to it) -> parallel PUTs to R2 -> confirm (queues resize + embed)
async function uploadProductPhotos(path: string, body: Record<string, unknown>, files: File[]): Promise<Product> {
	const photos = await Promise.all(files.map((f) => downscaleImage(f)));
	const tooBig = files.filter((_, i) => photos[i].size > MAX_PHOTO_BYTES).map((f) => f.name);
	if (tooBig.length) {
		const mb = MAX_PHOTO_BYTES / (1024 * 1024);
		throw new ApiError({
			code: "image_too_large",
			message: `${tooBig.join(", ")} ${tooBig.length === 1 ? "is" : "are"} larger than ${mb} MB even after resizing. Choose a smaller photo.`,
		});
	}
	const specs = photos.map((p, i) => ({ name: files[i].name, size: p.size, content_type: p.type }));
	const { data } = await apiFetch<ProductUploads>(path, { method: "POST", json: { ...body, files: specs } });
	await Promise.all(data.uploads.map((u, i) => putToR2(u, photos[i])));
	await apiFetch<ProductImage[]>("/api/kb/products/images/confirm/", {
		method: "POST",
		json: { image_ids: data.uploads.map((u) => u.image_id) },
	});
	return data.product;
}

export const featuresApi = {
	get: async (projectId: string) =>
		(await apiFetch<Features>(`/api/kb/features/?project_id=${encodeURIComponent(projectId)}`)).data,

	update: async (projectId: string, imageSearch: boolean) =>
		(await apiFetch<Features>("/api/kb/features/", {
			method: "PATCH",
			json: { project_id: projectId, image_search: imageSearch },
		})).data,
};

export type ProjectSettings = { project_id: string; name: string; currency: string };

export const projectSettingsApi = {
	get: async (projectId: string) =>
		(await apiFetch<ProjectSettings>(`/api/projects/settings/?project_id=${encodeURIComponent(projectId)}`)).data,

	update: async (projectId: string, data: { name?: string; currency?: string }) =>
		(await apiFetch<ProjectSettings>("/api/projects/settings/", {
			method: "PATCH",
			json: { project_id: projectId, ...data },
		})).data,
};

export const productApi = {
	list: async (projectId: string, kbId: string) =>
		(await apiFetch<Product[]>(
			`/api/kb/products/?project_id=${encodeURIComponent(projectId)}&kb_id=${encodeURIComponent(kbId)}`
		)).data,

	create: (projectId: string, kbId: string, name: string, description: string, files: File[]) =>
		uploadProductPhotos("/api/kb/products/", { project_id: projectId, kb_id: kbId, name, description }, files),

	update: (id: string, data: { name?: string; description?: string }) =>
		apiFetch<Product>(`/api/kb/products/${id}/`, { method: "PATCH", json: data }),

	patch: async (id: string, data: ProductPatch) =>
		(await apiFetch<Product>(`/api/kb/products/${id}/`, { method: "PATCH", json: data })).data,

	// kb_id omitted: the backend uses the project's default KB
	summary: async (projectId: string, kbId?: string) =>
		(await apiFetch<{ counts: Record<ProductStatus, number>; currency: string }>(
			`/api/kb/products/summary/?project_id=${encodeURIComponent(projectId)}${kbId ? `&kb_id=${encodeURIComponent(kbId)}` : ""}`
		)).data,

	remove: (id: string) => apiFetch<null>(`/api/kb/products/${id}/`, { method: "DELETE" }),

	addImages: (id: string, files: File[]) => uploadProductPhotos(`/api/kb/products/${id}/images/`, {}, files),

	removeImage: (imageId: string) => apiFetch<null>(`/api/kb/products/images/${imageId}/`, { method: "DELETE" }),
};

// Admin-only to change; any member can list
export const productFieldApi = {
	list: async (projectId: string, kbId: string) =>
		(await apiFetch<ProductField[]>(
			`/api/kb/product-fields/?project_id=${encodeURIComponent(projectId)}&kb_id=${encodeURIComponent(kbId)}`
		)).data,

	create: async (projectId: string, kbId: string, data: Partial<Omit<ProductField, "id" | "kb_id">>) =>
		(await apiFetch<ProductField>("/api/kb/product-fields/", { method: "POST", json: { project_id: projectId, kb_id: kbId, ...data } })).data,

	patch: async (id: string, data: Partial<Omit<ProductField, "id" | "kb_id">>) =>
		(await apiFetch<ProductField>(`/api/kb/product-fields/${id}/`, { method: "PATCH", json: data })).data,

	remove: (id: string) => apiFetch<null>(`/api/kb/product-fields/${id}/`, { method: "DELETE" }),
};

export type SourceResult = { created: number; updated: number; unchanged: number; hidden: number; images_queued?: number; rejected: { row: number; message: string }[] };

export type ProductSource = {
	id: string;
	kind: "csv" | "sheet" | "api";
	mapping: Record<string, string | null>;
	sheet_url: string;
	api_config: { method?: "GET" | "POST"; url?: string } | null;
	has_secret: boolean;
	last_synced_at: string | null;
	last_error: string;
	key_warning: boolean;
	created_at: string;
	auto_sync_enabled: boolean;
	schedule: "hourly" | "daily";
	next_sync_at: string | null;
	sync_failures: number;
	sync_running: boolean;
};

// The request a REST API source runs. The key is sent separately and never comes back.
export type ApiConfig = {
	method: "GET" | "POST";
	url: string;
	headers: Record<string, string>;
	query: Record<string, string>;
	auth_type: "none" | "bearer" | "header" | "query";
	auth_name: string;
	body: Record<string, unknown> | null;
	response_path: string;
	page_param: string;
	offset_param: string;
	size_param: string;
	page_size: number;
	max_pages: number;
};

export type MappingSuggestion = { target: string; column: string; confidence: number; reason: string };

export type CsvPreview = { headers: string[]; mapping: Record<string, string | null>; sample: Record<string, string>[]; key_warning: boolean };

export type ProductCandidate = {
	id: string;
	name: string;
	confidence: number;
	low_confidence: boolean;
	payload: { description?: string; sku?: string; external_id?: string | null; price?: string | null; image_srcs?: string[] };
	matched_product_id: string | null;
	changes: Record<string, { old: string | null; new: string | null }>;
	status: string;
	matched_by: "" | "external_id" | "sku" | "name";
};

export type LastExtraction = { status: string; pages: number; found: number; saved: number; skipped: number; finished_at: string } | Record<string, never>;
export type CandidatePage = { page_url: string; page_title: string; candidates: ProductCandidate[] };

// multipart: apiFetch forces a JSON content type, so the file upload sends its own request
async function uploadForm<T>(path: string, form: FormData): Promise<T> {
	const csrf = getCsrfToken();
	const res = await fetch(`${API_BASE_URL}${path}`, {
		method: "POST",
		body: form,
		credentials: "include",
		headers: csrf ? { "X-CSRFToken": csrf } : {},
	});
	const data = (await res.json()) as ApiResponse<T>;
	if (!res.ok || !data.success) {
		throw new ApiError(data.success ? { code: "request_failed", message: `Request failed with status ${res.status}` } : data.error);
	}
	return data.data;
}

const csvForm = (projectId: string, kbId: string, file: File, mapping?: Record<string, string | null>) => {
	const form = new FormData();
	form.append("project_id", projectId);
	form.append("kb_id", kbId);
	form.append("file", file);
	if (mapping) form.append("mapping", JSON.stringify(mapping));
	return form;
};

const q = (projectId: string, kbId: string) => `project_id=${encodeURIComponent(projectId)}&kb_id=${encodeURIComponent(kbId)}`;

// Admin-only
export const productSourceApi = {
	list: async (projectId: string, kbId: string) =>
		(await apiFetch<ProductSource[]>(`/api/kb/products/sources/?${q(projectId, kbId)}`)).data,

	previewCsv: (projectId: string, kbId: string, file: File, mapping?: Record<string, string | null>) =>
		uploadForm<CsvPreview>("/api/kb/products/sources/csv/preview/", csvForm(projectId, kbId, file, mapping)),

	createCsv: (projectId: string, kbId: string, file: File, mapping: Record<string, string | null>) =>
		uploadForm<{ source: ProductSource; result: SourceResult }>("/api/kb/products/sources/csv/", csvForm(projectId, kbId, file, mapping)),

	previewSheet: async (projectId: string, kbId: string, sheetUrl: string, mapping?: Record<string, string | null>) =>
		(await apiFetch<CsvPreview>("/api/kb/products/sources/sheet/preview/", {
			method: "POST",
			json: { project_id: projectId, kb_id: kbId, sheet_url: sheetUrl, ...(mapping ? { mapping } : {}) },
		})).data,

	createSheet: async (projectId: string, kbId: string, sheetUrl: string, mapping: Record<string, string | null>) =>
		(await apiFetch<{ source: ProductSource; result: SourceResult }>("/api/kb/products/sources/sheet/", {
			method: "POST",
			json: { project_id: projectId, kb_id: kbId, sheet_url: sheetUrl, mapping },
		})).data,

	sync: async (projectId: string, kbId: string, id: string) =>
		(await apiFetch<{ source: ProductSource; result: SourceResult }>(`/api/kb/products/sources/${id}/sync/`, {
			method: "POST",
			json: { project_id: projectId, kb_id: kbId },
		})).data,

	// A new version of a CSV source's file; the source's saved mapping applies
	reupload: (projectId: string, kbId: string, id: string, file: File) =>
		uploadForm<{ source: ProductSource; result: SourceResult }>(`/api/kb/products/sources/${id}/upload/`, csvForm(projectId, kbId, file)),

	previewApi: async (projectId: string, kbId: string, api: ApiConfig, secret: string, mapping?: Record<string, string | null>) =>
		(await apiFetch<CsvPreview>("/api/kb/products/sources/api/preview/", {
			method: "POST",
			json: { project_id: projectId, kb_id: kbId, api, secret, ...(mapping ? { mapping } : {}) },
		})).data,

	createApi: async (projectId: string, kbId: string, api: ApiConfig, secret: string, mapping: Record<string, string | null>) =>
		(await apiFetch<{ source: ProductSource; result: SourceResult }>("/api/kb/products/sources/api/", {
			method: "POST",
			json: { project_id: projectId, kb_id: kbId, api, secret, mapping },
		})).data,

	// AI suggestions for the previewed columns; nothing is saved
	suggestMapping: async (projectId: string, kbId: string, headers: string[], sample: Record<string, string>[]) =>
		(await apiFetch<{ suggestions: MappingSuggestion[] }>("/api/kb/products/sources/suggest-mapping/", {
			method: "POST",
			json: { project_id: projectId, kb_id: kbId, headers, sample },
		})).data.suggestions,

	// Automatic sync on/off and its schedule (Sheet and API sources)
	updateSchedule: async (projectId: string, kbId: string, id: string, body: { auto_sync_enabled?: boolean; schedule?: "hourly" | "daily" }) =>
		(await apiFetch<{ source: ProductSource }>(`/api/kb/products/sources/${id}/schedule/?${q(projectId, kbId)}`, {
			method: "PATCH",
			json: body,
		})).data,

	remove: (projectId: string, kbId: string, id: string) =>
		apiFetch<null>(`/api/kb/products/sources/${id}/?${q(projectId, kbId)}`, { method: "DELETE" }),
};

export const candidateApi = {
	list: async (projectId: string, kbId: string) =>
		(await apiFetch<{ pages: CandidatePage[]; ignored_pages: string[]; last_extraction: LastExtraction }>(`/api/kb/products/candidates/?${q(projectId, kbId)}`)).data,

	// Admin-only from here
	import: async (projectId: string, kbId: string, ids: string[]) =>
		(await apiFetch<{ imported: number; updated: number; rejected: { id: string; message: string }[] }>("/api/kb/products/candidates/import/", {
			method: "POST",
			json: { project_id: projectId, kb_id: kbId, ids },
		})).data,

	ignorePage: (projectId: string, kbId: string, pageUrl: string) =>
		apiFetch<{ ignored: number }>("/api/kb/products/candidates/ignore-page/", {
			method: "POST",
			json: { project_id: projectId, kb_id: kbId, page_url: pageUrl },
		}),

	extract: (projectId: string, kbId: string) =>
		apiFetch<{ queued: boolean }>("/api/kb/products/extract/", { method: "POST", json: { project_id: projectId, kb_id: kbId } }),
};
