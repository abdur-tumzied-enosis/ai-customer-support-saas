const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

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
};

// Document API functions
export const documentApi = {
	upload: async (file: File, projectId: string, kbName: string = "default") => {
		const formData = new FormData();
		formData.append("file", file);
		formData.append("project_id", projectId);
		formData.append("kb_name", kbName);

		const csrfToken = getCsrfToken();
		const headers: HeadersInit = {
			...(csrfToken ? { "X-CSRFToken": csrfToken } : {}),
		};

		const res = await fetch(`${API_BASE_URL}/api/kb/documents/upload/`, {
			method: "POST",
			headers,
			credentials: "include",
			body: formData,
		});

		const data = (await res.json()) as ApiResponse<{
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
		}>;

		if (!res.ok || !data.success) {
			if (!data.success) {
				throw new ApiError(data.error);
			}
			throw new ApiError({
				code: "upload_failed",
				message: `Upload failed with status ${res.status}`,
			});
		}

		return data;
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
};

// Custom Instruction type
export type CustomInstruction = {
	id: string;
	label: string;
	message: string;
	knowledge_base_id: string | null;
	knowledge_base_name: string | null;
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
	 * Get the effective instructions for a KB (KB-level if exists, else project-level).
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
	update: async (instructionId: string, data: { label?: string; message?: string }) => {
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

// Channel API functions
export const channelApi = {
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

// Conversation API functions
export const conversationApi = {
	list: async (projectSlug: string) => {
		return apiFetch<{
			conversations: Array<{
				id: string;
				name: string;
				knowledge_base_id: string;
				knowledge_base_name: string;
				message_count: number;
				last_message: {
					id: string;
					text: string;
					created_at: string;
				} | null;
				created_at: string;
				updated_at: string;
			}>;
		}>(`/api/channels/conversations?project_slug=${encodeURIComponent(projectSlug)}`);
	},

	get: async (conversationId: string) => {
		return apiFetch<{
			conversation: {
				id: string;
				name: string;
				knowledge_base_id: string;
				knowledge_base_name: string;
				created_at: string;
				updated_at: string;
			};
		}>(`/api/channels/conversations/${conversationId}`);
	},

	create: async (data: {
		project_slug: string;
		name?: string;
		knowledge_base_id?: string;
	}) => {
		return apiFetch<{
			conversation: {
				id: string;
				name: string;
				knowledge_base_id: string;
				knowledge_base_name: string;
				created_at: string;
				updated_at: string;
			};
		}>("/api/channels/conversations", {
			method: "POST",
			json: data,
		});
	},

	update: async (conversationId: string, data: { name?: string }) => {
		return apiFetch<{
			conversation: {
				id: string;
				name: string;
				knowledge_base_id: string;
				knowledge_base_name: string;
				created_at: string;
				updated_at: string;
			};
		}>(`/api/channels/conversations/${conversationId}`, {
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
			messages: Array<{
				id: string;
				direction: "in" | "out";
				status: "pending" | "processing" | "completed" | "failed";
				text: string;
				created_at: string;
				agent_reply_id: string | null;
			}>;
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
		return apiFetch<{
			message: {
				id: string;
				direction: "in" | "out";
				status: "pending" | "processing" | "completed" | "failed";
				text: string;
				created_at: string;
			};
		}>(`/api/channels/conversations/${conversationId}/messages`, {
			method: "POST",
			json: { text },
		});
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
};

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
	token_usage: {
		total: number;
		input_tokens: number;
		output_tokens: number;
		embedding_tokens: number;
		by_kind: Record<string, number>;
		trend: Array<{
			date: string;
			total_tokens: number;
		}>;
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