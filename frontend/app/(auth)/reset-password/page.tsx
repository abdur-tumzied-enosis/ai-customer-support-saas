"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle, CardFooter } from "@/components/ui/card";
import { useToast } from "@/components/ui/toast";
import { authApi } from "@/lib/api";
import { formatError } from "@/lib/errors";

// One page, two steps: no uid/token → ask for the email; the emailed link brings uid+token → set a new password
function ResetPasswordForm() {
	const params = useSearchParams();
	const uid = params.get("uid");
	const token = params.get("token");
	const [email, setEmail] = useState("");
	const [password, setPassword] = useState("");
	const [confirmPassword, setConfirmPassword] = useState("");
	const toast = useToast();
	const [success, setSuccess] = useState("");
	const [loading, setLoading] = useState(false);

	async function handleSubmit(e: React.FormEvent) {
		e.preventDefault();
		if (uid && token && password !== confirmPassword) {
			toast.error("Passwords do not match");
			return;
		}
		setLoading(true);
		try {
			const res = uid && token
				? await authApi.confirmPasswordReset({ uid, token, new_password: password })
				: await authApi.requestPasswordReset(email.trim());
			setSuccess(res.message);
			toast.success(res.message);
		} catch (err) {
			toast.error(formatError(err));
		} finally {
			setLoading(false);
		}
	}

	return (
		<Card>
			<CardHeader className="space-y-1">
				<CardTitle className="text-2xl">Reset password</CardTitle>
				<CardDescription>
					{uid && token ? "Choose a new password" : "Enter your email to receive a password reset link"}
				</CardDescription>
			</CardHeader>
			<CardContent>
				{success ? (
					<p className="text-sm">{success}</p>
				) : (
					<form onSubmit={handleSubmit} className="space-y-4">
						{uid && token ? (
							<>
								<div className="space-y-2">
									<Label htmlFor="password">New password</Label>
									<Input
										id="password"
										type="password"
										autoComplete="new-password"
										value={password}
										onChange={(e) => setPassword(e.target.value)}
										required
										minLength={8}
										disabled={loading}
									/>
								</div>
								<div className="space-y-2">
									<Label htmlFor="confirm-password">Confirm password</Label>
									<Input
										id="confirm-password"
										type="password"
										autoComplete="new-password"
										value={confirmPassword}
										onChange={(e) => setConfirmPassword(e.target.value)}
										required
										disabled={loading}
									/>
								</div>
							</>
						) : (
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
						)}
						<Button type="submit" className="w-full" disabled={loading}>
							{loading ? "Sending..." : uid && token ? "Set new password" : "Send reset link"}
						</Button>
					</form>
				)}
			</CardContent>
			<CardFooter className="flex flex-col space-y-4">
				<div className="text-sm text-center text-muted-foreground">
					{success && uid ? "Ready?" : "Remember your password?"}{" "}
					<Link href="/sign-in" className="text-primary hover:underline font-medium">
						Sign in
					</Link>
				</div>
			</CardFooter>
		</Card>
	);
}

export default function ResetPasswordPage() {
	return (
		<Suspense>
			<ResetPasswordForm />
		</Suspense>
	);
}
