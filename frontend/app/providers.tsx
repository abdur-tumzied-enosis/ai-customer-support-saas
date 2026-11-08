"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ReactNode, useState } from "react";
import { ThemeProvider } from "next-themes";
import { AuthProvider } from "@/contexts/AuthContext";
import { ToastProvider } from "@/components/ui/toast";

type ProvidersProps = {
	children: ReactNode;
};

export default function Providers({ children }: ProvidersProps) {
	const [queryClient] = useState(() => new QueryClient());
	return (
		<ThemeProvider attribute="class" defaultTheme="system" enableSystem>
			<QueryClientProvider client={queryClient}>
				<ToastProvider>
					<AuthProvider>{children}</AuthProvider>
				</ToastProvider>
			</QueryClientProvider>
		</ThemeProvider>
	);
}


