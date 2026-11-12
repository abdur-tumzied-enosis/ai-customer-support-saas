import type { Metadata } from "next";

export const metadata: Metadata = {
	title: "Create account",
	description: "Create a TumarAgent account and start answering customers on WhatsApp and Messenger with free credits.",
	robots: { index: false, follow: false },
};

// Page body is client-rendered; the H1 here is what the server HTML contains.
export default function Layout({ children }: { children: React.ReactNode }) {
	return (
		<>
			<h1 className="sr-only">Create your account</h1>
			{children}
		</>
	);
}
