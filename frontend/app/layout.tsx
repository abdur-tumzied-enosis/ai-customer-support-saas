import "./globals.css";
import type { Metadata } from "next";
import { Inter } from "next/font/google";
import Providers from "./providers";
import CsrfTokenFetcher from "@/components/CsrfTokenFetcher";

const inter = Inter({ subsets: ["latin"] });

export const metadata: Metadata = {
	metadataBase: new URL("https://tumaragent.com"),
	title: { default: "TumarAgent", template: "%s · TumarAgent" },
	description: "Multi-channel AI assistant dashboard",
	applicationName: "TumarAgent",
	openGraph: { siteName: "TumarAgent", title: "TumarAgent", description: "Multi-channel AI assistant dashboard", url: "/" },
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


