"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Download, Loader2, Trash2 } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { authApi } from "@/lib/api";
import { formatError } from "@/lib/errors";
import { useAuth } from "@/hooks/useAuth";

type Status = { kind: "ok" | "error"; text: string } | null;

function StatusLine({ status }: { status: Status }) {
	if (!status) return null;
	return (
		<Alert variant={status.kind === "error" ? "destructive" : "default"}>
			<AlertDescription>{status.text}</AlertDescription>
		</Alert>
	);
}

export default function SettingsPage() {
	const { user, checkAuth } = useAuth();
	const router = useRouter();

	const [firstName, setFirstName] = useState(user?.first_name ?? "");
	const [lastName, setLastName] = useState(user?.last_name ?? "");
	const [profileStatus, setProfileStatus] = useState<Status>(null);
	const [savingProfile, setSavingProfile] = useState(false);

	const [currentPassword, setCurrentPassword] = useState("");
	const [newPassword, setNewPassword] = useState("");
	const [confirmPassword, setConfirmPassword] = useState("");
	const [passwordStatus, setPasswordStatus] = useState<Status>(null);
	const [savingPassword, setSavingPassword] = useState(false);

	const [deleteOpen, setDeleteOpen] = useState(false);
	const [deletePassword, setDeletePassword] = useState("");
	const [deleteError, setDeleteError] = useState("");
	const [deleting, setDeleting] = useState(false);

	async function saveProfile(e: React.FormEvent) {
		e.preventDefault();
		setSavingProfile(true);
		setProfileStatus(null);
		try {
			await authApi.updateProfile({ first_name: firstName.trim(), last_name: lastName.trim() });
			await checkAuth();
			setProfileStatus({ kind: "ok", text: "Profile saved." });
		} catch (err) {
			setProfileStatus({ kind: "error", text: formatError(err) });
		} finally {
			setSavingProfile(false);
		}
	}

	async function changePassword(e: React.FormEvent) {
		e.preventDefault();
		if (newPassword !== confirmPassword) {
			setPasswordStatus({ kind: "error", text: "New passwords do not match." });
			return;
		}
		setSavingPassword(true);
		setPasswordStatus(null);
		try {
			await authApi.changePassword({ current_password: currentPassword, new_password: newPassword });
			setCurrentPassword("");
			setNewPassword("");
			setConfirmPassword("");
			setPasswordStatus({ kind: "ok", text: "Password changed. Other devices have been signed out." });
		} catch (err) {
			setPasswordStatus({ kind: "error", text: formatError(err) });
		} finally {
			setSavingPassword(false);
		}
	}

	async function deleteAccount(e: React.FormEvent) {
		e.preventDefault();
		setDeleting(true);
		setDeleteError("");
		try {
			await authApi.deleteAccount(deletePassword);
			await checkAuth();
			router.push("/");
		} catch (err) {
			setDeleteError(formatError(err));
			setDeleting(false);
		}
	}

	return (
		<div className="max-w-2xl space-y-6">
			<div>
				<h1 className="text-2xl font-semibold">Account settings</h1>
				<p className="text-sm text-muted-foreground">Signed in as {user?.email}</p>
			</div>

			<Card>
				<CardHeader>
					<CardTitle>Profile</CardTitle>
				</CardHeader>
				<CardContent>
					<form onSubmit={saveProfile} className="space-y-4">
						<StatusLine status={profileStatus} />
						<div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
							<div className="space-y-1">
								<Label htmlFor="first-name">First name</Label>
								<Input id="first-name" required maxLength={150} value={firstName} onChange={(e) => setFirstName(e.target.value)} />
							</div>
							<div className="space-y-1">
								<Label htmlFor="last-name">Last name</Label>
								<Input id="last-name" required maxLength={150} value={lastName} onChange={(e) => setLastName(e.target.value)} />
							</div>
						</div>
						<div className="space-y-1">
							<Label htmlFor="email">Email</Label>
							<Input id="email" value={user?.email ?? ""} disabled />
							<p className="text-xs text-muted-foreground">Contact support to change your email address.</p>
						</div>
						<Button type="submit" disabled={savingProfile}>
							{savingProfile && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
							Save profile
						</Button>
					</form>
				</CardContent>
			</Card>

			<Card>
				<CardHeader>
					<CardTitle>Password</CardTitle>
				</CardHeader>
				<CardContent>
					<form onSubmit={changePassword} className="space-y-4">
						<StatusLine status={passwordStatus} />
						<div className="space-y-1">
							<Label htmlFor="current-password">Current password</Label>
							<Input
								id="current-password"
								type="password"
								autoComplete="current-password"
								required
								value={currentPassword}
								onChange={(e) => setCurrentPassword(e.target.value)}
							/>
						</div>
						<div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
							<div className="space-y-1">
								<Label htmlFor="new-password">New password</Label>
								<Input
									id="new-password"
									type="password"
									autoComplete="new-password"
									required
									minLength={8}
									value={newPassword}
									onChange={(e) => setNewPassword(e.target.value)}
								/>
							</div>
							<div className="space-y-1">
								<Label htmlFor="confirm-password">Confirm new password</Label>
								<Input
									id="confirm-password"
									type="password"
									autoComplete="new-password"
									required
									value={confirmPassword}
									onChange={(e) => setConfirmPassword(e.target.value)}
								/>
							</div>
						</div>
						<Button type="submit" disabled={savingPassword}>
							{savingPassword && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
							Change password
						</Button>
					</form>
				</CardContent>
			</Card>

			<Card>
				<CardHeader>
					<CardTitle>Your data</CardTitle>
					<CardDescription>
						Download everything in your projects as JSON: knowledge bases, instructions, widgets, contacts and
						conversations. Channel access tokens are left out.
					</CardDescription>
				</CardHeader>
				<CardContent>
					<Button variant="outline" asChild>
						<a href={authApi.exportUrl}>
							<Download className="mr-2 h-4 w-4" />
							Export data
						</a>
					</Button>
				</CardContent>
			</Card>

			<Card className="border-destructive/50">
				<CardHeader>
					<CardTitle className="text-destructive">Delete account</CardTitle>
					<CardDescription>
						Permanently deletes your account. Projects only you belong to are deleted with all their knowledge bases,
						documents, widgets and conversations. Projects shared with teammates stay with them. This cannot be undone.
					</CardDescription>
				</CardHeader>
				<CardContent>
					<Button variant="destructive" onClick={() => setDeleteOpen(true)}>
						<Trash2 className="mr-2 h-4 w-4" />
						Delete account
					</Button>
				</CardContent>
			</Card>

			<Dialog
				open={deleteOpen}
				onOpenChange={(open) => {
					if (deleting) return;
					setDeleteOpen(open);
					setDeletePassword("");
					setDeleteError("");
				}}
			>
				<DialogContent>
					<form onSubmit={deleteAccount} className="space-y-4">
						<DialogHeader>
							<DialogTitle>Delete your account?</DialogTitle>
							<DialogDescription>
								Everything listed above is deleted permanently. Export your data first if you want a copy. Enter your
								password to confirm.
							</DialogDescription>
						</DialogHeader>
						{deleteError && (
							<Alert variant="destructive">
								<AlertDescription>{deleteError}</AlertDescription>
							</Alert>
						)}
						<div className="space-y-1">
							<Label htmlFor="delete-password">Password</Label>
							<Input
								id="delete-password"
								type="password"
								autoComplete="current-password"
								required
								value={deletePassword}
								onChange={(e) => setDeletePassword(e.target.value)}
							/>
						</div>
						<DialogFooter>
							<Button type="button" variant="outline" disabled={deleting} onClick={() => setDeleteOpen(false)}>
								Cancel
							</Button>
							<Button type="submit" variant="destructive" disabled={deleting || !deletePassword}>
								{deleting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
								Delete permanently
							</Button>
						</DialogFooter>
					</form>
				</DialogContent>
			</Dialog>
		</div>
	);
}
