"use client";

import { createContext, useContext, useEffect, useState, ReactNode } from "react";
import { authApi } from "@/lib/api";

type User = {
	id: number;
	username: string;
	email: string;
	first_name: string;
	last_name: string;
};

type Project = {
	id: number;
	name: string;
	slug: string;
	role: string;
};

type AuthState = {
	user: User | null;
	projects: Project[];
	loading: boolean;
	authenticated: boolean;
};

type AuthContextType = AuthState & {
	checkAuth: () => Promise<void>;
};

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
	const [authState, setAuthState] = useState<AuthState>({
		user: null,
		projects: [],
		loading: true,
		authenticated: false,
	});

	async function checkAuth() {
		try {
			const response = await authApi.getCurrentUser();
			setAuthState({
				user: response.data.user,
				projects: response.data.projects,
				loading: false,
				authenticated: true,
			});
		} catch (error) {
			setAuthState({
				user: null,
				projects: [],
				loading: false,
				authenticated: false,
			});
		}
	}

	useEffect(() => {
		checkAuth();
	}, []);

	return (
		<AuthContext.Provider value={{ ...authState, checkAuth }}>
			{children}
		</AuthContext.Provider>
	);
}

export function useAuth() {
	const context = useContext(AuthContext);
	if (context === undefined) {
		throw new Error("useAuth must be used within an AuthProvider");
	}
	return context;
}

