"use client";

import { useState } from "react";
import Link from "next/link";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle, CardFooter } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { useRouter } from "next/navigation";
import { authApi } from "@/lib/api";
import { formatError } from "@/lib/errors";
import { useRedirectIfAuthenticated, useAuth } from "@/hooks/useAuth";

export default function SignInPage() {
	const router = useRouter();
	const { loading: authLoading } = useRedirectIfAuthenticated();
	const { checkAuth } = useAuth();
	const [email, setEmail] = useState("");
	const [password, setPassword] = useState("");
	const [error, setError] = useState("");
	const [loading, setLoading] = useState(false);

	if (authLoading) {
		return (
			<div className="flex items-center justify-center min-h-[400px]">
				<div className="text-muted-foreground">Loading...</div>
			</div>
		);
	}

	async function handleSubmit(e: React.FormEvent) {
		e.preventDefault();
		setError("");
		setLoading(true);

		try {
			await authApi.login({
				email: email.trim(),
				password,
			});

			// Refresh auth state in context
			await checkAuth();

			// Redirect to dashboard on success
			router.push("/dashboard");
		} catch (err) {
			setError(formatError(err));
		} finally {
			setLoading(false);
		}
	}

	return (
		<Card>
			<CardHeader className="space-y-1">
				<CardTitle className="text-2xl">Sign in</CardTitle>
				<CardDescription>
					Enter your credentials to access your account
				</CardDescription>
			</CardHeader>
			<CardContent>
				<form onSubmit={handleSubmit} className="space-y-4">
					{error && (
						<Alert variant="destructive">
							<AlertDescription>{error}</AlertDescription>
						</Alert>
					)}
					<div className="space-y-2">
						<Label htmlFor="email">Email</Label>
						<Input
							id="email"
							type="email"
							placeholder="you@example.com"
							value={email}
							onChange={(e) => setEmail(e.target.value)}
							required
							disabled={loading}
						/>
					</div>
					<div className="space-y-2">
						<div className="flex items-center justify-between">
							<Label htmlFor="password">Password</Label>
							<Link
								href="/reset-password"
								className="text-sm text-primary hover:underline"
							>
								Forgot password?
							</Link>
						</div>
						<Input
							id="password"
							type="password"
							value={password}
							onChange={(e) => setPassword(e.target.value)}
							required
							disabled={loading}
						/>
					</div>
					<Button type="submit" className="w-full" disabled={loading}>
						{loading ? "Signing in..." : "Sign in"}
					</Button>
				</form>
			</CardContent>
			<CardFooter className="flex flex-col space-y-4">
				<div className="text-sm text-center text-muted-foreground">
					Don&apos;t have an account?{" "}
					<Link href="/sign-up" className="text-primary hover:underline font-medium">
						Sign up
					</Link>
				</div>
			</CardFooter>
		</Card>
	);
}


