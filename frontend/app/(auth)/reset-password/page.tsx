"use client";

import { useState } from "react";
import Link from "next/link";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle, CardFooter } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";

export default function ResetPasswordPage() {
	const [email, setEmail] = useState("");
	const [loading, setLoading] = useState(false);
	const [success, setSuccess] = useState(false);

	async function handleSubmit(e: React.FormEvent) {
		e.preventDefault();
		setLoading(true);

		// TODO: Replace with actual API call
		try {
			// Simulate API call
			await new Promise((resolve) => setTimeout(resolve, 1000));
			setSuccess(true);
		} catch (err) {
			// Handle error
		} finally {
			setLoading(false);
		}
	}

	return (
		<Card>
			<CardHeader className="space-y-1">
				<CardTitle className="text-2xl">Reset password</CardTitle>
				<CardDescription>
					Enter your email to receive a password reset link
				</CardDescription>
			</CardHeader>
			<CardContent>
				{success ? (
					<Alert>
						<AlertDescription>
							If an account with that email exists, we've sent you a password reset link.
						</AlertDescription>
					</Alert>
				) : (
					<form onSubmit={handleSubmit} className="space-y-4">
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
						<Button type="submit" className="w-full" disabled={loading}>
							{loading ? "Sending..." : "Send reset link"}
						</Button>
					</form>
				)}
			</CardContent>
			<CardFooter className="flex flex-col space-y-4">
				<div className="text-sm text-center text-muted-foreground">
					Remember your password?{" "}
					<Link href="/sign-in" className="text-primary hover:underline font-medium">
						Sign in
					</Link>
				</div>
			</CardFooter>
		</Card>
	);
}


