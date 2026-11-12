import Link from "next/link";
import ThemeToggle from "@/components/theme/ThemeToggle";
import SiteFooter from "@/components/layout/SiteFooter";
import { site } from "@/lib/site";

// Same shell as the legal pages, minus the "Last updated" line, plus list and code styles for step-by-step guides.
export default function DocsLayout({ children }: { children: React.ReactNode }) {
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
				<article className="mx-auto max-w-3xl text-[15px] leading-7 text-foreground/90 [&_h1]:text-3xl [&_h1]:font-bold [&_h1]:text-foreground [&_h2]:mt-12 [&_h2]:scroll-mt-20 [&_h2]:border-t [&_h2]:border-border [&_h2]:pt-8 [&_h2]:text-2xl [&_h2]:font-semibold [&_h2]:text-foreground [&_h3]:mt-8 [&_h3]:scroll-mt-20 [&_h3]:text-lg [&_h3]:font-semibold [&_h3]:text-foreground [&_p]:mt-4 [&_ul]:mt-4 [&_ul]:list-disc [&_ul]:pl-6 [&_ol]:mt-4 [&_ol]:list-decimal [&_ol]:pl-6 [&_li]:mt-2 [&_a]:text-primary [&_a]:underline [&_code]:rounded [&_code]:bg-muted [&_code]:px-1.5 [&_code]:py-0.5 [&_code]:text-[13px] [&_table]:mt-4 [&_table]:w-full [&_table]:text-sm [&_th]:border [&_th]:border-border [&_th]:bg-muted/50 [&_th]:p-2 [&_th]:text-left [&_td]:border [&_td]:border-border [&_td]:p-2 [&_td]:align-top">
					{children}
				</article>
			</main>
			<SiteFooter />
		</div>
	);
}
