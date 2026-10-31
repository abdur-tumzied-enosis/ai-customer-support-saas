import Link from "next/link";
import ThemeToggle from "@/components/theme/ThemeToggle";
import SiteFooter from "@/components/layout/SiteFooter";
import { site } from "@/lib/site";

export default function LegalLayout({ children }: { children: React.ReactNode }) {
	return (
		<div className="min-h-screen flex flex-col bg-background">
			<header className="border-b border-border">
				<div className="container mx-auto px-6 py-4 flex items-center justify-between">
					<Link href="/" className="text-lg font-semibold">
						{site.productName}
					</Link>
					<ThemeToggle />
				</div>
			</header>
			<main className="flex-1 px-6 py-12">
				{/* ponytail: child selectors instead of @tailwindcss/typography for four static pages */}
				<article className="mx-auto max-w-3xl text-[15px] leading-7 text-foreground/90 [&_h1]:text-3xl [&_h1]:font-bold [&_h1]:text-foreground [&_h2]:mt-10 [&_h2]:text-xl [&_h2]:font-semibold [&_h2]:text-foreground [&_h3]:mt-6 [&_h3]:font-semibold [&_h3]:text-foreground [&_p]:mt-4 [&_ul]:mt-4 [&_ul]:list-disc [&_ul]:pl-6 [&_li]:mt-1 [&_a]:text-primary [&_a]:underline [&_table]:mt-4 [&_table]:w-full [&_th]:border [&_th]:border-border [&_th]:p-2 [&_th]:text-left [&_td]:border [&_td]:border-border [&_td]:p-2 [&_td]:align-top">
					<p className="!mt-0 text-sm text-muted-foreground">Last updated: {site.legalUpdated}</p>
					{children}
				</article>
			</main>
			<SiteFooter />
		</div>
	);
}
