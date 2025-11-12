"use client";

import { useRouter } from "next/navigation";
import { useRequireAuth } from "@/hooks/useAuth";
import Sidebar from "@/components/layout/Sidebar";
import ThemeToggle from "@/components/theme/ThemeToggle";
import { Input } from "@/components/ui/input";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuLabel,
	DropdownMenuSeparator,
	DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { LogOut, User, Settings } from "lucide-react";
import { authApi } from "@/lib/api";

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
	const router = useRouter();
	const { user, loading } = useRequireAuth();

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
			<main className="flex-1">
				<header className="border-b bg-background border-border">
					<div className="max-w-6xl mx-auto px-6 py-3 flex items-center justify-between">
						<div className="text-sm text-muted-foreground">Auto Magic Pro</div>
						<div className="flex items-center gap-3">
							<Input
								placeholder="Search"
								className="hidden md:block w-64"
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

