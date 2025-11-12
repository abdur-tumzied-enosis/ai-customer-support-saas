"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

const links = [
	{ href: "/dashboard", label: "Dashboard" },
	{ href: "/conversations", label: "Conversations" },
	{ href: "/knowledge", label: "Knowledge" },
	{ href: "/channels", label: "Channels" },
	{ href: "/analytics", label: "Analytics" },
	{ href: "/settings", label: "Settings" },
];

export default function Sidebar() {
	const pathname = usePathname();
	return (
		<aside className="hidden md:flex md:w-64 border-r bg-background border-border">
			<nav className="flex-1 p-4">
				<div className="text-sm text-muted-foreground px-2 mb-2">Auto Magic Pro</div>
				<ul className="space-y-1">
					{links.map((l) => {
						const active = pathname === l.href || (l.href !== "/" && pathname.startsWith(l.href));
						return (
							<li key={l.href}>
								<Link
									href={l.href}
									className={cn(
										"block rounded-md px-3 py-2 text-sm transition-colors",
										active
											? "bg-primary text-primary-foreground"
											: "text-foreground hover:bg-accent hover:text-accent-foreground"
									)}
								>
									{l.label}
								</Link>
							</li>
						);
					})}
				</ul>
			</nav>
		</aside>
	);
}


