"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useRequireAuth } from "@/hooks/useAuth";
import Sidebar from "@/components/layout/Sidebar";
import MobileSidebar from "@/components/layout/MobileSidebar";
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
import { LogOut, User, Settings, Menu } from "lucide-react";
import { authApi } from "@/lib/api";

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
	const router = useRouter();
	const { user, loading } = useRequireAuth();
	const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

	async function handleLogout() {
		try {
			await authApi.logout();
		} catch (err) {
			// Even if logout fails, redirect to sign-in
			console.error("Logout error:", err);
		} finally {
			router.push("/sign-in");
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
							<span className="text-sm font-medium text-foreground md:hidden">Auto Magic Pro</span>
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
											<AvatarFallback>AM</AvatarFallback>
										</Avatar>
									</button>
								</DropdownMenuTrigger>
								<DropdownMenuContent align="end" className="w-56">
									<DropdownMenuLabel>My Account</DropdownMenuLabel>
									<DropdownMenuSeparator />
									<DropdownMenuItem>
										<User className="mr-2 h-4 w-4" />
										<span>Profile</span>
									</DropdownMenuItem>
									<DropdownMenuItem>
										<Settings className="mr-2 h-4 w-4" />
										<span>Settings</span>
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

