import "./globals.css";
import type { Metadata } from "next";
import { Inter } from "next/font/google";
import Providers from "./providers";
import CsrfTokenFetcher from "@/components/CsrfTokenFetcher";

const inter = Inter({ subsets: ["latin"] });

export const metadata: Metadata = {
	title: "Auto Magic Pro",
	description: "Multi-channel AI assistant dashboard",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
	return (
		<html lang="en" suppressHydrationWarning>
			<body className={inter.className}>
				<Providers>
					<CsrfTokenFetcher />
					{children}
				</Providers>
			</body>
		</html>
	);
}


