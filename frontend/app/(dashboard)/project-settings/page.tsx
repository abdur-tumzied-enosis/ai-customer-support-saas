"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Loader2 } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useToast } from "@/components/ui/toast";
import { featuresApi, projectSettingsApi, type Features } from "@/lib/api";
import { CURRENCIES } from "@/lib/currencies";
import { formatError } from "@/lib/errors";
import { useAuth } from "@/hooks/useAuth";


export default function ProjectSettingsPage() {
	const toast = useToast();
	const { projects, checkAuth } = useAuth();
	const project = projects.length > 0 ? projects[0] : null;
	const isAdmin = project?.role === "admin";

	const [name, setName] = useState("");
	const [currency, setCurrency] = useState("BDT");
	const [filter, setFilter] = useState("");
	const [saving, setSaving] = useState(false);

	const [features, setFeatures] = useState<Features | null>(null);
	const [savingFeatures, setSavingFeatures] = useState(false);

	useEffect(() => {
		if (!project) return;
		const id = String(project.id);
		projectSettingsApi
			.get(id)
			.then((s) => {
				setName(s.name);
				setCurrency(s.currency);
			})
			.catch((err) => toast.error(formatError(err)));
		featuresApi
			.get(id)
			.then(setFeatures)
			.catch(() => setFeatures(null));
	}, [project]);

	async function save(e: React.FormEvent) {
		e.preventDefault();
		if (!project) return;
		setSaving(true);
		try {
			const s = await projectSettingsApi.update(String(project.id), { name: name.trim(), currency });
			setName(s.name);
			setCurrency(s.currency);
			await checkAuth();
			toast.success("Project settings saved.");
		} catch (err) {
			toast.error(formatError(err));
		} finally {
			setSaving(false);
		}
	}

	async function toggleImageSearch() {
		if (!project || !features) return;
		setSavingFeatures(true);
		try {
			setFeatures(await featuresApi.update(String(project.id), { image_search: !features.image_search }));
		} catch (err) {
			toast.error(formatError(err));
		} finally {
			setSavingFeatures(false);
		}
	}

	async function toggleVisionFallback() {
		if (!project || !features) return;
		setSavingFeatures(true);
		try {
			setFeatures(await featuresApi.update(String(project.id), { vision_fallback: !features.vision_fallback }));
		} catch (err) {
			toast.error(formatError(err));
		} finally {
			setSavingFeatures(false);
		}
	}

	const query = filter.trim().toLowerCase();
	const matches = CURRENCIES.filter(
		(c) => !query || c.code.toLowerCase().includes(query) || c.name.toLowerCase().includes(query)
	);
	const selected = CURRENCIES.find((c) => c.code === currency);

	return (
		<div className="mx-auto max-w-2xl space-y-6">
			<h1 className="text-2xl font-semibold">Project settings</h1>

			<Card>
				<CardHeader>
					<CardTitle>General</CardTitle>
					<CardDescription>
						The project name and the currency shown next to prices.{" "}
						{!isAdmin && "Only project admins can change these."}
					</CardDescription>
				</CardHeader>
				<CardContent>
					<form onSubmit={save} className="space-y-4">
						<div className="space-y-2">
							<Label htmlFor="project-name">Project name</Label>
							<Input
								id="project-name"
								required
								maxLength={255}
								disabled={!isAdmin}
								value={name}
								onChange={(e) => setName(e.target.value)}
							/>
						</div>
						<div className="space-y-2">
							<Label htmlFor="currency-filter">
								Currency: {selected ? `${selected.symbol} ${selected.code}` : currency}
							</Label>
							<Input
								id="currency-filter"
								placeholder="Search currencies"
								disabled={!isAdmin}
								value={filter}
								onChange={(e) => setFilter(e.target.value)}
							/>
							<div className="max-h-48 space-y-1 overflow-y-auto rounded-md border p-1">
								{matches.length === 0 && <p className="p-2 text-sm text-muted-foreground">No match.</p>}
								{matches.map((c) => (
									<Button
										key={c.code}
										type="button"
										variant={c.code === currency ? "default" : "ghost"}
										className="w-full justify-start"
										disabled={!isAdmin}
										onClick={() => setCurrency(c.code)}
									>
										{c.symbol} {c.code} <span className="ml-2 opacity-70">{c.name}</span>
									</Button>
								))}
							</div>
						</div>
						{isAdmin && (
							<Button type="submit" disabled={saving}>
								{saving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
								Save
							</Button>
						)}
					</form>
				</CardContent>
			</Card>

			<Card>
				<CardHeader>
					<CardTitle>Orders</CardTitle>
					<CardDescription>Let the assistant take orders, and manage the orders list.</CardDescription>
				</CardHeader>
				<CardContent>
					<Link href="/project-settings/orders" className="text-sm text-primary underline">
						Order settings
					</Link>
				</CardContent>
			</Card>

			{features?.image_search_available && (
				<Card>
					<CardHeader>
						<CardTitle>Image search</CardTitle>
						<CardDescription>
							Lets the assistant recognise products from photos customers send. Turn it on per channel too.
						</CardDescription>
					</CardHeader>
					<CardContent className="space-y-4">
						{isAdmin ? (
							<Button
								type="button"
								variant={features.image_search ? "default" : "outline"}
								disabled={savingFeatures}
								onClick={toggleImageSearch}
							>
								{savingFeatures && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
								Image search: {features.image_search ? "On" : "Off"}
							</Button>
						) : (
							<p className="text-sm">Image search: {features.image_search ? "On" : "Off"}</p>
						)}
					</CardContent>
				</Card>
			)}

			{features?.vision_fallback_available && (
				<Card>
					<CardHeader>
						<CardTitle>Photo help</CardTitle>
						<CardDescription>
							When the assistant cannot match a photo to your catalog, or a customer message reports damage, a return or a complaint, it can send the photo to the configured AI provider so it can look at it. Photos may contain personal information. Only photos that need help are sent. Turn on image search per channel too.
						</CardDescription>
					</CardHeader>
					<CardContent className="space-y-4">
						{isAdmin ? (
							<Button
								type="button"
								variant={features.vision_fallback ? "default" : "outline"}
								disabled={savingFeatures || (!features.image_search && !features.vision_fallback)}
								onClick={toggleVisionFallback}
							>
								{savingFeatures && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
								Photo help: {features.vision_fallback ? "On" : "Off"}
							</Button>
						) : (
							<p className="text-sm">Photo help: {features.vision_fallback ? "On" : "Off"}</p>
						)}
						{!features.image_search && (
							<p className="text-sm">Turn on Image search first. Photo help cannot run without it.</p>
						)}
					</CardContent>
				</Card>
			)}
		</div>
	);
}
