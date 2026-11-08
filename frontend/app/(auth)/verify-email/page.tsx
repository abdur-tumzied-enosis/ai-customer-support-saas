"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle, CardFooter } from "@/components/ui/card";
import { useToast } from "@/components/ui/toast";
import { authApi } from "@/lib/api";
import { formatError } from "@/lib/errors";
import { useAuth } from "@/hooks/useAuth";

function VerifyEmailForm() {
	const router = useRouter();
	const { checkAuth } = useAuth();
	const email = useSearchParams().get("email") || "";
	const [code, setCode] = useState("");
	const toast = useToast();
	const [loading, setLoading] = useState(false);

	async function handleSubmit(e: React.FormEvent) {
		e.preventDefault();
		setLoading(true);
		try {
			await authApi.verifyEmail({ email, code: code.trim() });
			await checkAuth();
			router.push("/dashboard");
		} catch (err) {
			toast.error(formatError(err));
		} finally {
			setLoading(false);
		}
	}

	async function handleResend() {
		try {
			const res = await authApi.resendVerification(email);
			toast.success(res.message);
		} catch (err) {
			toast.error(formatError(err));
		}
	}

	return (
		<Card>
			<CardHeader className="space-y-1">
				<CardTitle className="text-2xl">Verify your email</CardTitle>
				<CardDescription>
					We sent a 6-digit code to {email || "your email"}. It expires in 10 minutes.
				</CardDescription>
			</CardHeader>
			<CardContent>
				<form onSubmit={handleSubmit} className="space-y-4">
					<div className="space-y-2">
						<Label htmlFor="code">Verification code</Label>
						<Input
							id="code"
							inputMode="numeric"
							autoComplete="one-time-code"
							pattern="\d{6}"
							maxLength={6}
							placeholder="123456"
							value={code}
							onChange={(e) => setCode(e.target.value)}
							required
							disabled={loading}
						/>
					</div>
					<Button type="submit" className="w-full" disabled={loading || !email}>
						{loading ? "Verifying..." : "Verify"}
					</Button>
				</form>
			</CardContent>
			<CardFooter className="flex flex-col space-y-4">
				<div className="text-sm text-center text-muted-foreground">
					Didn&apos;t get it?{" "}
					<button type="button" onClick={handleResend} disabled={!email} className="text-primary hover:underline font-medium">
						Resend code
					</button>
					{" · "}
					<Link href="/sign-in" className="text-primary hover:underline font-medium">
						Sign in
					</Link>
				</div>
			</CardFooter>
		</Card>
	);
}

export default function VerifyEmailPage() {
	return (
		<Suspense>
			<VerifyEmailForm />
		</Suspense>
	);
}
