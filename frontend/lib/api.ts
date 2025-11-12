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