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

export default function SignUpPage() {
	const router = useRouter();
	const { loading: authLoading } = useRedirectIfAuthenticated();
	const { checkAuth } = useAuth();
	const [firstName, setFirstName] = useState("");
	const [lastName, setLastName] = useState("");
	const [businessName, setBusinessName] = useState("");
	const [email, setEmail] = useState("");
	const [password, setPassword] = useState("");
	const [confirmPassword, setConfirmPassword] = useState("");
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

		if (!firstName.trim()) {
			setError("First name is required");
			return;
		}

		if (!lastName.trim()) {
			setError("Last name is required");
			return;
		}

		if (password !== confirmPassword) {
			setError("Passwords do not match");
			return;
		}

		if (password.length < 8) {
			setError("Password must be at least 8 characters long");
			return;
		}

		setLoading(true);

		try {
			await authApi.signup({
				first_name: firstName.trim(),
				last_name: lastName.trim(),
				// business_name: businessName.trim() || undefined,
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
				<CardTitle className="text-2xl">Create account</CardTitle>
				<CardDescription>
					Enter your information to create a new account
				</CardDescription>
			</CardHeader>
			<CardContent>
				<form onSubmit={handleSubmit} className="space-y-4">
					{error && (
						<Alert variant="destructive">
							<AlertDescription>{error}</AlertDescription>
						</Alert>
					)}
					<div className="grid grid-cols-2 gap-4">
						<div className="space-y-2">
							<Label htmlFor="firstName">First Name</Label>
							<Input
								id="firstName"
								type="text"
								placeholder="John"
								value={firstName}
								onChange={(e) => setFirstName(e.target.value)}
								required
								disabled={loading}
							/>
						</div>
						<div className="space-y-2">
							<Label htmlFor="lastName">Last Name</Label>
							<Input
								id="lastName"
								type="text"
								placeholder="Doe"
								value={lastName}
								onChange={(e) => setLastName(e.target.value)}
								required
								disabled={loading}
							/>
						</div>
					</div>
					
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
						<Label htmlFor="password">Password</Label>
						<Input
							id="password"
							type="password"
							value={password}
							onChange={(e) => setPassword(e.target.value)}
							required
							disabled={loading}
							minLength={8}
						/>
						<p className="text-xs text-muted-foreground">
							Must be at least 8 characters long
						</p>
					</div>
					<div className="space-y-2">
						<Label htmlFor="confirm-password">Confirm Password</Label>
						<Input
							id="confirm-password"
							type="password"
							value={confirmPassword}
							onChange={(e) => setConfirmPassword(e.target.value)}
							required
							disabled={loading}
						/>
					</div>
					<Button type="submit" className="w-full" disabled={loading}>
						{loading ? "Creating account..." : "Create account"}
					</Button>
				</form>
			</CardContent>
			<CardFooter className="flex flex-col space-y-4">
				<div className="text-sm text-center text-muted-foreground">
					Already have an account?{" "}
					<Link href="/sign-in" className="text-primary hover:underline font-medium">
						Sign in
					</Link>
				</div>
			</CardFooter>
		</Card>
	);
}


