"use client";

import { useEffect, useState, type ReactNode } from "react";
import type { Route } from "next";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { ListOrdered, Settings, type LucideIcon } from "lucide-react";
import { orderApi } from "@/lib/orders-api";
import { useAuth } from "@/hooks/useAuth";
import { cn } from "@/lib/utils";

export const ORDERS_CHANGED = "orders:changed";

function NavLink({ href, active, icon: Icon, children }: { href: string; active: boolean; icon?: LucideIcon; children: ReactNode }) {
	return (
		<Link
			href={href as Route}
			aria-current={active ? "page" : undefined}
			className={cn(
				"flex items-center gap-2 rounded-md px-3 py-2 text-sm transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
				active ? "bg-accent font-medium" : "text-muted-foreground",
			)}
		>
			{Icon && <Icon className="h-4 w-4 shrink-0" />}
			{children}
		</Link>
	);
}

// Side nav shared by the Orders list and Order settings. The setup flow has its own layout and does not use this.
export default function OrdersShell({ children }: { children: ReactNode }) {
	const pathname = usePathname();
	const { projects } = useAuth();
	const project = projects.length > 0 ? projects[0] : null;
	const [enabled, setEnabled] = useState<boolean | null>(null);

	useEffect(() => {
		if (!project) return;
		const load = () =>
			orderApi.settings(String(project.id)).then((res) => setEnabled(res.data.enabled)).catch(() => setEnabled(null));
		load();
		window.addEventListener(ORDERS_CHANGED, load);
		return () => window.removeEventListener(ORDERS_CHANGED, load);
	}, [project, pathname]);

	const isAdmin = project?.role === "admin";

	return (
		<div className="flex flex-col gap-6 md:flex-row md:gap-10">
			<nav aria-label="Orders" className="shrink-0 space-y-4 md:sticky md:top-4 md:w-56 md:self-start">
				<h1 className="text-2xl font-semibold">Orders</h1>
				{enabled !== null && (
					<p
						className={cn(
							"inline-flex items-center gap-2 rounded-full border px-3 py-1 text-sm font-medium",
							enabled ? "border-green-600/40 bg-green-600/10 text-green-700 dark:text-green-400" : "text-muted-foreground",
						)}
					>
						<span className={cn("h-2 w-2 rounded-full", enabled ? "bg-green-600" : "bg-muted-foreground/50")} />
						{enabled ? "Taking orders" : "Orders are off"}
					</p>
				)}
				<div className="space-y-1">
					<NavLink href="/orders" active={pathname === "/orders"} icon={ListOrdered}>
						Orders
					</NavLink>
					{isAdmin && (
						<NavLink href="/project-settings/orders" active={pathname.startsWith("/project-settings/orders")} icon={Settings}>
							Order settings
						</NavLink>
					)}
				</div>
			</nav>
			<div className="min-w-0 flex-1">{children}</div>
		</div>
	);
}
