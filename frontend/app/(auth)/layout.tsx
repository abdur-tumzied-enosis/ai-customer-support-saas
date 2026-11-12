import Link from "next/link";
import ThemeToggle from "@/components/theme/ThemeToggle";
import SiteFooter from "@/components/layout/SiteFooter";
import Logo from "@/components/layout/Logo";
import { AppProviders } from "@/app/providers";

export default function AuthLayout({ children }: { children: React.ReactNode }) {
	return (
		<AppProviders>
		<div className="min-h-screen flex flex-col bg-background">
			<header className="border-b border-border">
				<div className="container mx-auto px-6 py-4 flex items-center justify-between">
					<Link href="/" className="text-foreground hover:no-underline">
						<Logo className="h-6" />
					</Link>
					<ThemeToggle />
				</div>
			</header>
			<main className="flex-1 flex items-center justify-center p-6">
				<div className="w-full max-w-md">{children}</div>
			</main>
			<SiteFooter />
		</div>
		</AppProviders>
	);
}

