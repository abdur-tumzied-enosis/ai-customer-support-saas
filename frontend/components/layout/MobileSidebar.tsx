"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
	LayoutDashboard,
	MessageSquare,
	BookOpen,
	Radio,
	MessagesSquare,
	BarChart3,
	X,
} from "lucide-react";

const links = [
	{ href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
	{ href: "/conversations", label: "Conversations", icon: MessageSquare },
	{ href: "/knowledge", label: "Knowledge", icon: BookOpen },
	{ href: "/channels", label: "Channels", icon: Radio },
	{ href: "/widgets", label: "Website widget", icon: MessagesSquare },
	{ href: "/analytics", label: "Analytics", icon: BarChart3 },
] as const;

interface MobileSidebarProps {
	open: boolean;
	onOpenChange: (open: boolean) => void;
}

export default function MobileSidebar({ open, onOpenChange }: MobileSidebarProps) {
	const pathname = usePathname();

	if (!open) return null;

	return (
		<>
			{/* Backdrop */}
			<div
				className="fixed inset-0 bg-black/50 z-50 md:hidden"
				onClick={() => onOpenChange(false)}
				aria-hidden="true"
			/>

			{/* Sidebar */}
			<aside
				className={cn(
					"fixed inset-y-0 left-0 w-72 bg-background border-r border-border z-50 md:hidden",
					"transform transition-transform duration-300 ease-in-out",
					open ? "translate-x-0" : "-translate-x-full"
				)}
			>
				{/* Header */}
				<div className="flex items-center justify-between h-14 border-b border-border px-4">
					<span className="text-sm font-semibold text-foreground">
						Auto Magic Pro
					</span>
					<Button
						variant="ghost"
						size="icon"
						onClick={() => onOpenChange(false)}
						className="h-8 w-8"
						aria-label="Close menu"
					>
						<X className="h-5 w-5" />
					</Button>
				</div>

				{/* Navigation */}
				<nav className="flex-1 p-3">
					<ul className="space-y-1">
						{links.map((l) => {
							const active = pathname.startsWith(l.href);
							const Icon = l.icon;

							return (
								<li key={l.href}>
									<Link
										href={l.href}
										onClick={() => onOpenChange(false)}
										className={cn(
											"flex items-center gap-3 rounded-md px-3 py-3 text-sm font-medium transition-colors",
											active
												? "bg-primary text-primary-foreground shadow-sm"
												: "text-muted-foreground hover:bg-accent hover:text-foreground"
										)}
									>
										<Icon className="h-5 w-5 shrink-0" />
										<span>{l.label}</span>
									</Link>
								</li>
							);
						})}
					</ul>
				</nav>
			</aside>
		</>
	);
}

