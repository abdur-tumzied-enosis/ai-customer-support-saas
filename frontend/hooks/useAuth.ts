"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/contexts/AuthContext";

// Re-export useAuth from context for backward compatibility
export { useAuth } from "@/contexts/AuthContext";

export function useRequireAuth() {
	const { user, loading, authenticated } = useAuth();
	const router = useRouter();

	useEffect(() => {
		if (!loading && !authenticated) {
			router.push("/sign-in");
		}
	}, [loading, authenticated, router]);

	return { user, loading, authenticated };
}

export function useRedirectIfAuthenticated() {
	const { user, loading, authenticated } = useAuth();
	const router = useRouter();

	useEffect(() => {
		if (!loading && authenticated) {
			router.push("/dashboard");
		}
	}, [loading, authenticated, router]);

	return { user, loading, authenticated };
}

