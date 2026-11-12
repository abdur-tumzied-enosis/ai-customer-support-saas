import type { Metadata } from "next";

export const metadata: Metadata = {
	title: "Reset password",
	description: "Reset your TumarAgent account password.",
	robots: { index: false, follow: false },
};

// Page body is client-rendered; the H1 here is what the server HTML contains.
export default function Layout({ children }: { children: React.ReactNode }) {
	return (
		<>
			<h1 className="sr-only">Reset password</h1>
			{children}
		</>
	);
}
