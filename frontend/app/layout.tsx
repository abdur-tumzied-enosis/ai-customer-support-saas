import "./globals.css";
import type { Metadata } from "next";
import { Inter } from "next/font/google";
import Providers from "./providers";

const inter = Inter({ subsets: ["latin"] });

export const metadata: Metadata = {
	metadataBase: new URL("https://tumaragent.com"),
	title: { default: "TumarAgent: AI Customer Support on WhatsApp & Messenger in Bangla", template: "%s · TumarAgent" },
	description: "AI agent that answers your customers on WhatsApp, Messenger and Instagram in Bangla and English, 24/7.",
	applicationName: "TumarAgent",
	openGraph: { siteName: "TumarAgent", url: "/" },
	verification: {
		other: {
			"facebook-domain-verification": "qosq3kxdp7mblk7gvu4z8wi63m5swz",
		},
	},
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
	return (
		<html lang="en" suppressHydrationWarning>
			<body className={inter.className}>
				<Providers>
					{children}
				</Providers>
			</body>
		</html>
	);
}


