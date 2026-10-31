"use client";

import { useEffect } from "react";
import { API_BASE_URL } from "@/lib/api";

/**
 * Component that ensures CSRF cookie is set on app load.
 * This should be included in the root layout to ensure CSRF token is available
 * for all POST/PUT/DELETE requests.
 */
export default function CsrfTokenFetcher() {
	useEffect(() => {
		// Fetch CSRF token on mount to ensure cookie is set
		fetch(`${API_BASE_URL}/api/auth/csrf/`, {
			method: "GET",
			credentials: "include",
		}).catch(() => {
			// Ignore errors - this is just to set the cookie
		});
	}, []);

	return null;
}

