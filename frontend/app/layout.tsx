import "./globals.css";
import type { Metadata } from "next";
import { Inter } from "next/font/google";
import Providers from "./providers";
import Script from "next/script";

const GTM_ID = "GTM-NV47CNFN";
const GA_MEASUREMENT_ID = "G-VXB2TRJD4C";
// Note: with the GA snippet live below, do NOT also add a GA4 tag inside the GTM container — that would double-count.

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
				{/* Google Tag Manager (noscript fallback for no-JS browsers) */}
				<noscript>
					<iframe
						src={`https://www.googletagmanager.com/ns.html?id=${GTM_ID}`}
						height="0"
						width="0"
						style={{ display: "none", visibility: "hidden" }}
					/>
				</noscript>
				<Script id="gtm-init" strategy="afterInteractive">
					{`(function(w,d,s,l,i){w[l]=w[l]||[];w[l].push({'gtm.start':
					new Date().getTime(),event:'gtm.js'});var f=d.getElementsByTagName(s)[0],
					j=d.createElement(s),dl=l!='dataLayer'?'&l='+l:'';j.async=true;j.src=
					'https://www.googletagmanager.com/gtm.js?id='+i+dl;f.parentNode.insertBefore(j,f);
					})(window,document,'script','dataLayer','${GTM_ID}');`}
				</Script>
				{/* Google Analytics (direct gtag) */}
				<Script async src={`https://www.googletagmanager.com/gtag/js?id=${GA_MEASUREMENT_ID}`} strategy="afterInteractive" />
				<Script id="gtag-init" strategy="afterInteractive">
					{`window.dataLayer = window.dataLayer || [];
					function gtag(){dataLayer.push(arguments);}
					gtag('js', new Date());
					gtag('config', '${GA_MEASUREMENT_ID}');`}
				</Script>

				<Providers>
					{children}
				</Providers>
			</body>
		</html>
	);
}
