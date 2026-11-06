"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, useEffect } from "react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/hooks/useAuth";
import { conversationApi, productApi } from "@/lib/api";
import Logo from "@/components/layout/Logo";
import {
	LayoutDashboard,
	MessageSquare,
	BookOpen,
	Radio,
	MessagesSquare,
	BarChart3,
	ChevronLeft,
	ChevronRight,
} from "lucide-react";

const links = [
	{ href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
	{ href: "/conversations", label: "Conversations", icon: MessageSquare },
	{ href: "/knowledge", label: "Knowledge", icon: BookOpen },
	{ href: "/channels", label: "Channels", icon: Radio },
	{ href: "/widgets", label: "Website widget", icon: MessagesSquare },
	{ href: "/analytics", label: "Analytics", icon: BarChart3 },
] as const;

const SIDEBAR_COLLAPSED_KEY = "sidebar-collapsed";

export default function Sidebar() {
	const pathname = usePathname();
	const [isCollapsed, setIsCollapsed] = useState(false);
	const [isMounted, setIsMounted] = useState(false);
	const { projects } = useAuth();
	const [needsHuman, setNeedsHuman] = useState(0);
	const [needsInfo, setNeedsInfo] = useState(0);
	const projectSlug = projects && projects.length > 0 ? projects[0].slug : "";

	// ponytail: refreshed on navigation only; add polling/websocket if staff need live counts
	useEffect(() => {
		if (!projectSlug) return;
		conversationApi
			.list(projectSlug, { needsHuman: true })
			.then((r) => setNeedsHuman(r.data.needs_human_count))
			.catch(() => {});
	}, [projectSlug, pathname]);

	const projectId = projects && projects.length > 0 ? String(projects[0].id) : "";
	useEffect(() => {
		if (!projectId) return;
		productApi
			.summary(projectId)
			.then((r) => setNeedsInfo(r.counts.needs_info))
			.catch(() => {});
	}, [projectId, pathname]);

	// Load collapsed state from localStorage on mount
	useEffect(() => {
		setIsMounted(true);
		const stored = localStorage.getItem(SIDEBAR_COLLAPSED_KEY);
		if (stored !== null) {
			setIsCollapsed(stored === "true");
		}
	}, []);

	// Save collapsed state to localStorage
	const toggleCollapse = () => {
		const newValue = !isCollapsed;
		setIsCollapsed(newValue);
		localStorage.setItem(SIDEBAR_COLLAPSED_KEY, String(newValue));
	};

	// Prevent hydration mismatch by not rendering until mounted
	if (!isMounted) {
		return (
			<aside className="hidden md:flex md:w-64 border-r bg-background border-border">
				<nav className="flex-1 p-4">
					<Logo variant="icon" className="h-6 mx-2 mb-2 text-muted-foreground" />
				</nav>
			</aside>
		);
	}

	return (
		<aside
			className={cn(
				"hidden md:flex flex-col border-r bg-background border-border transition-all duration-300 ease-in-out",
				isCollapsed ? "w-[68px]" : "w-64"
			)}
		>
			{/* Header */}
			<div className={cn(
				"flex items-center h-14 border-b border-border px-3",
				isCollapsed ? "justify-center" : "justify-between"
			)}>
				{!isCollapsed && <Logo className="h-5 text-foreground" />}
				<Button
					variant="ghost"
					size="icon"
					onClick={toggleCollapse}
					className="h-8 w-8 shrink-0"
					aria-label={isCollapsed ? "Expand sidebar" : "Collapse sidebar"}
				>
					{isCollapsed ? (
						<ChevronRight className="h-4 w-4" />
					) : (
						<ChevronLeft className="h-4 w-4" />
					)}
				</Button>
			</div>

			{/* Navigation */}
			<nav className="flex-1 p-2">
				<ul className="space-y-1">
					{links.map((l) => {
						const active = pathname.startsWith(l.href);
						const Icon = l.icon;

						return (
							<li key={l.href}>
								<Link
									href={l.href}
									className={cn(
										"flex items-center gap-3 rounded-md px-3 py-2.5 text-sm font-medium transition-all duration-200",
										isCollapsed && "justify-center px-2",
										active
											? "bg-primary text-primary-foreground shadow-sm"
											: "text-muted-foreground hover:bg-accent hover:text-foreground"
									)}
									title={isCollapsed ? l.label : undefined}
								>
									<Icon className={cn("h-5 w-5 shrink-0", isCollapsed && "h-5 w-5")} />
									{!isCollapsed && (
										<span className="truncate">{l.label}</span>
									)}
									{l.href === "/conversations" && needsHuman > 0 && (
										<span
											className={cn(
												"rounded-full bg-destructive px-1.5 text-xs font-semibold text-destructive-foreground",
												!isCollapsed && "ml-auto"
											)}
											aria-label={`${needsHuman} conversations need a human`}
										>
											{needsHuman}
										</span>
									)}
									{l.href === "/knowledge" && needsInfo > 0 && (
										<span
											className={cn(
												"rounded-full bg-destructive px-1.5 text-xs font-semibold text-destructive-foreground",
												!isCollapsed && "ml-auto"
											)}
											aria-label={`${needsInfo} products need info`}
										>
											{needsInfo}
										</span>
									)}
								</Link>
							</li>
						);
					})}
				</ul>
			</nav>

			{/* Footer - collapse hint */}
			{!isCollapsed && (
				<div className="p-3 border-t border-border">
					<p className="text-xs text-muted-foreground text-center">
						Press to collapse
					</p>
				</div>
			)}
		</aside>
	);
}
