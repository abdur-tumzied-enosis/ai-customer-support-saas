"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ReactNode, useState } from "react";
import { ThemeProvider } from "next-themes";
import { AuthProvider } from "@/contexts/AuthContext";
import { ToastProvider } from "@/components/ui/toast";
import CsrfTokenFetcher from "@/components/CsrfTokenFetcher";

// Theme only: public pages (home, legal) must not load auth/query/toast or call the API.
export default function Providers({ children }: { children: ReactNode }) {
	return (
		<ThemeProvider attribute="class" defaultTheme="system" enableSystem>
			{children}
		</ThemeProvider>
	);
}

// Auth, data and toast state for the sign-in/up and dashboard areas only.
export function AppProviders({ children }: { children: ReactNode }) {
	const [queryClient] = useState(() => new QueryClient());
	return (
		<QueryClientProvider client={queryClient}>
			<ToastProvider>
				<AuthProvider>
					<CsrfTokenFetcher />
					{children}
				</AuthProvider>
			</ToastProvider>
		</QueryClientProvider>
	);
}
