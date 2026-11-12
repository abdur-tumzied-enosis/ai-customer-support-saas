import type { Metadata } from "next";

export const metadata: Metadata = {
	title: "Sign in",
	description: "Sign in to your TumarAgent account to manage your AI customer support agent.",
	robots: { index: false, follow: false },
};

// Page body is client-rendered; the H1 here is what the server HTML contains.
export default function Layout({ children }: { children: React.ReactNode }) {
	return (
		<>
			<h1 className="sr-only">Sign in</h1>
			{children}
		</>
	);
}
