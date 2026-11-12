import type { Metadata } from "next";

export const metadata: Metadata = {
	title: "Verify email",
	description: "Confirm your email address to finish creating your TumarAgent account.",
	robots: { index: false, follow: false },
};

// Page body is client-rendered; the H1 here is what the server HTML contains.
export default function Layout({ children }: { children: React.ReactNode }) {
	return (
		<>
			<h1 className="sr-only">Verify email</h1>
			{children}
		</>
	);
}
