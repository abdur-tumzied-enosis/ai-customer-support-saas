import Link from "next/link";
import { site } from "@/lib/site";

const links = [
	{ href: "/privacy", label: "Privacy" },
	{ href: "/terms", label: "Terms" },
	{ href: "/dpa", label: "DPA" },
	{ href: "/data-deletion", label: "Data deletion" },
	{ href: "/docs/connect-channels", label: "Channel setup guide" },
] as const;

export default function SiteFooter() {
	return (
		<footer className="border-t border-border py-8 px-6">
			<div className="container mx-auto max-w-6xl flex flex-col md:flex-row items-center justify-between gap-4 text-sm">
				<div className="text-muted-foreground">
					© {new Date().getFullYear()} {site.legalName}
				</div>
				<nav className="flex flex-wrap items-center justify-center gap-x-6 gap-y-2">
					{links.map((l) => (
						<Link key={l.href} href={l.href} className="text-muted-foreground hover:text-foreground">
							{l.label}
						</Link>
					))}
					<a href={`mailto:${site.supportEmail}`} className="text-muted-foreground hover:text-foreground">
						Contact
					</a>
				</nav>
			</div>
		</footer>
	);
}
