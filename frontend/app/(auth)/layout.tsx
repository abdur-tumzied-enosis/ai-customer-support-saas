import Link from "next/link";
import ThemeToggle from "@/components/theme/ThemeToggle";

export default function AuthLayout({ children }: { children: React.ReactNode }) {
	return (
		<div className="min-h-screen flex flex-col bg-background">
			<header className="border-b border-border">
				<div className="container mx-auto px-6 py-4 flex items-center justify-between">
					<Link href="/" className="text-lg font-semibold">
						Auto Magic Pro
					</Link>
					<ThemeToggle />
				</div>
			</header>
			<main className="flex-1 flex items-center justify-center p-6">
				<div className="w-full max-w-md">{children}</div>
			</main>
		</div>
	);
}

