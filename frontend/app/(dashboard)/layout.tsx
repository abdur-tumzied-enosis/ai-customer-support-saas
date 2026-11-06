"use client";

import { useState } from "react";
import { useRequireAuth } from "@/hooks/useAuth";
import Sidebar from "@/components/layout/Sidebar";
import MobileSidebar from "@/components/layout/MobileSidebar";
import Logo from "@/components/layout/Logo";
import ThemeToggle from "@/components/theme/ThemeToggle";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuLabel,
	DropdownMenuSeparator,
	DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import Link from "next/link";
import { LogOut, Settings, Menu, Building2 } from "lucide-react";
import { authApi } from "@/lib/api";

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
	const { user, loading } = useRequireAuth();
	const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

	async function handleLogout() {
		try {
			await authApi.logout();
		} catch (err) {
			// Even if logout fails, redirect home
			console.error("Logout error:", err);
		} finally {
			// Full reload resets AuthContext; client-side push kept stale `authenticated` and bounced to /dashboard
			window.location.href = "/";
		}
	}

	// Show loading state while checking authentication
	if (loading) {
		return (
			<div className="min-h-screen flex items-center justify-center bg-background">
				<div className="text-center">
					<div className="text-muted-foreground">Loading...</div>
				</div>
			</div>
		);
	}

	// Don't render dashboard if not authenticated (useRequireAuth will redirect)
	if (!user) {
		return null;
	}

	return (
		<div className="min-h-screen flex bg-background text-foreground">
			<Sidebar />
			<MobileSidebar open={mobileMenuOpen} onOpenChange={setMobileMenuOpen} />
			<main className="flex-1 min-w-0">
				<header className="border-b bg-background border-border sticky top-0 z-40">
					<div className="max-w-6xl mx-auto px-4 md:px-6 py-3 flex items-center justify-between">
						<div className="flex items-center gap-3">
							<Button
								variant="ghost"
								size="icon"
								className="md:hidden"
								onClick={() => setMobileMenuOpen(true)}
								aria-label="Open menu"
							>
								<Menu className="h-5 w-5" />
							</Button>
							<Logo variant="icon" className="h-6 text-foreground md:hidden" />
						</div>
						<div className="flex items-center gap-3">
							<Input
								placeholder="Search"
								className="hidden lg:block w-64"
							/>
							<ThemeToggle />
							<DropdownMenu>
								<DropdownMenuTrigger asChild>
									<button className="rounded-full focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2">
										<Avatar>
											<AvatarFallback>{((user.first_name?.[0] || "") + (user.last_name?.[0] || "")).toUpperCase() || "?"}</AvatarFallback>
										</Avatar>
									</button>
								</DropdownMenuTrigger>
								<DropdownMenuContent align="end" className="w-56">
									<DropdownMenuLabel className="font-normal">
									<div className="truncate font-medium">{user.first_name} {user.last_name}</div>
									<div className="truncate text-xs text-muted-foreground">{user.email}</div>
								</DropdownMenuLabel>
									<DropdownMenuSeparator />
									<DropdownMenuItem asChild>
										<Link href="/settings">
											<Settings className="mr-2 h-4 w-4" />
											<span>Account settings</span>
										</Link>
									</DropdownMenuItem>
									<DropdownMenuItem asChild>
										<Link href="/project-settings">
											<Building2 className="mr-2 h-4 w-4" />
											<span>Project settings</span>
										</Link>
									</DropdownMenuItem>
									<DropdownMenuSeparator />
									<DropdownMenuItem onClick={handleLogout} className="text-destructive focus:text-destructive">
										<LogOut className="mr-2 h-4 w-4" />
										<span>Log out</span>
									</DropdownMenuItem>
								</DropdownMenuContent>
							</DropdownMenu>
						</div>
					</div>
				</header>
				<div className="max-w-6xl mx-auto p-6">{children}</div>
			</main>
		</div>
	);
}

