"use client";

import { useEffect, useState } from "react";
import { Loader2, Pencil, Plus, Trash2 } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { useToast } from "@/components/ui/toast";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import CopyField from "@/components/channels/CopyField";
import { featuresApi, knowledgeBaseApi, widgetApi, type KnowledgeBase, type Widget, type WidgetAppearance } from "@/lib/api";
import { kbOptionLabel, pickKb } from "@/lib/knowledgeBase";
import { useAuth } from "@/hooks/useAuth";
import { formatError } from "@/lib/errors";

// Same as channels_app.widget.APPEARANCE_DEFAULTS
const DEFAULT_APPEARANCE: WidgetAppearance = {
	title: "Chat with us",
	welcome_message: "Hi! How can we help you today?",
	primary_color: "#2563eb",
	position: "right",
	privacy_url: "",
	show_branding: true,
};

type Form = {
	name: string;
	knowledge_base_id: string;
	is_active: boolean;
	origins: string; // one per line
	appearance: WidgetAppearance;
};

export default function WidgetsPage() {
	const toast = useToast();
	const { projects } = useAuth();
	const projectSlug = projects.length > 0 ? projects[0].slug : "";
	const projectId = projects.length > 0 ? String(projects[0].id) : "";

	const [widgets, setWidgets] = useState<Widget[]>([]);
	const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
	const [loading, setLoading] = useState(false);

	const [editing, setEditing] = useState<Widget | "new" | null>(null);
	const [tab, setTab] = useState("general");
	const [form, setForm] = useState<Form | null>(null);
	const [formError, setFormError] = useState<string | null>(null);
	const [saving, setSaving] = useState(false);
	const [deleting, setDeleting] = useState<Widget | null>(null);

	useEffect(() => {
		if (!projectSlug || !projectId) return;
		load();
		knowledgeBaseApi
			.list(projectId)
			.then((r) => setKnowledgeBases(r.data.knowledge_bases))
			.catch((err) => toast.error(formatError(err)));
	}, [projectSlug, projectId]);

	const [imageSearchOn, setImageSearchOn] = useState(false);
	useEffect(() => {
		if (!projectId) return;
		featuresApi
			.get(projectId)
			.then((f) => setImageSearchOn(f.image_search))
			.catch(() => setImageSearchOn(false));
	}, [projectId]);

	const toggleImageSearch = async (widget: Widget) => {
		try {
			await widgetApi.update(widget.id, { image_search_enabled: !widget.image_search_enabled });
			load();
		} catch (err) {
			toast.error(formatError(err));
		}
	};

	const load = async () => {
		setLoading(true);
		try {
			setWidgets((await widgetApi.list(projectSlug)).data.widgets);
		} catch (err) {
			toast.error(formatError(err));
		} finally {
			setLoading(false);
		}
	};

	const openEditor = (widget: Widget | null) => {
		setForm(
			widget
				? {
						name: widget.name,
						knowledge_base_id: widget.knowledge_base.id,
						is_active: widget.is_active,
						origins: widget.allowed_origins.join("\n"),
						appearance: widget.appearance,
					}
				: {
						name: "",
						knowledge_base_id: pickKb(knowledgeBases, null)?.id ?? "",
						is_active: true,
						origins: "",
						appearance: DEFAULT_APPEARANCE,
					}
		);
		setFormError(null);
		setTab("general");
		setEditing(widget ?? "new");
	};

	const save = async () => {
		if (!form || !editing) return;
		if (!form.name.trim()) {
			setFormError("Please enter a name");
			setTab("general");
			return;
		}
		setSaving(true);
		setFormError(null);
		const data = {
			name: form.name,
			knowledge_base_id: form.knowledge_base_id || undefined,
			is_active: form.is_active,
			allowed_origins: form.origins.split(/[\s,]+/).filter(Boolean),
			appearance: form.appearance,
		};
		try {
			const saved = (editing === "new" ? await widgetApi.create(projectSlug, data) : await widgetApi.update(editing.id, data)).data.widget;
			setEditing(saved);
			setForm({ ...form, origins: saved.allowed_origins.join("\n") }); // show them normalized
			if (editing === "new") setTab("install"); // next step: copy the snippet
			toast.success(editing === "new" ? "Widget created. Copy the install code below." : "Widget saved.");
			load();
		} catch (err) {
			setFormError(formatError(err));
		} finally {
			setSaving(false);
		}
	};

	const closeEditor = () => {
		setEditing(null);
	};

	const toggleActive = async (widget: Widget) => {
		try {
			await widgetApi.update(widget.id, { is_active: !widget.is_active });
			load();
		} catch (err) {
			toast.error(formatError(err));
		}
	};

	const remove = async () => {
		if (!deleting) return;
		try {
			await widgetApi.remove(deleting.id);
			toast.success("Widget deleted. Its conversations stay in the inbox.");
			setDeleting(null);
			load();
		} catch (err) {
			toast.error(formatError(err));
		}
	};

	const selectedKb = knowledgeBases.find((kb) => kb.id === form?.knowledge_base_id);
	const savedWidget = editing && editing !== "new" ? editing : null;
	const backendOrigin = savedWidget ? new URL(savedWidget.snippet.match(/src="([^"]+)"/)?.[1] ?? "http://localhost").origin : "";
	const setAppearance = (patch: Partial<WidgetAppearance>) => form && setForm({ ...form, appearance: { ...form.appearance, ...patch } });

	return (
		<div className="space-y-6">
			<div className="flex flex-wrap items-center justify-between gap-4">
				<div>
					<h1 className="text-2xl font-semibold">Website widget</h1>
					<p className="text-sm text-muted-foreground">
						Add an AI chat to any website with one line of code. Visitor chats show up in Conversations.
					</p>
				</div>
				<Button onClick={() => openEditor(null)}>
					<Plus className="mr-2 h-4 w-4" /> New widget
				</Button>
			</div>


			{loading && widgets.length === 0 ? (
				<div className="flex justify-center p-8">
					<Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
				</div>
			) : widgets.length === 0 ? (
				<Card>
					<CardContent className="p-8 text-center text-sm text-muted-foreground">
						No widgets yet. Create one, add your website&apos;s address, and paste the install code into your site.
					</CardContent>
				</Card>
			) : (
				<div className="space-y-3">
					{widgets.map((widget) => (
						<Card key={widget.id}>
							<CardContent className="flex flex-wrap items-center justify-between gap-4 p-4">
								<div className="space-y-1">
									<div className="flex flex-wrap items-center gap-2 font-medium">
										<span
											className="inline-block h-3 w-3 rounded-full"
											style={{ background: widget.appearance.primary_color }}
											aria-hidden
										/>
										{widget.name}
										<Badge variant={widget.is_active ? "default" : "outline"}>{widget.is_active ? "Active" : "Disabled"}</Badge>
										<Badge variant="secondary">{widget.knowledge_base.name}</Badge>
									</div>
									<div className="text-xs text-muted-foreground">
										{widget.allowed_origins.length > 0 ? (
											widget.allowed_origins.join(", ")
										) : (
											<span className="text-destructive">No websites allowed yet: the widget shows nowhere</span>
										)}
									</div>
								</div>
								<div className="flex gap-2">
									<Button variant="outline" size="sm" onClick={() => toggleActive(widget)}>
										{widget.is_active ? "Disable" : "Enable"}
									</Button>
									{imageSearchOn && projects[0]?.role !== "admin" && (
										<span className="self-center text-xs text-muted-foreground">
											Image search: {widget.image_search_enabled ? "On" : "Off"}
										</span>
									)}
									{imageSearchOn && projects[0]?.role === "admin" && (
										<Button
											variant={widget.image_search_enabled ? "default" : "outline"}
											size="sm"
											onClick={() => toggleImageSearch(widget)}
										>
											Image search: {widget.image_search_enabled ? "On" : "Off"}
										</Button>
									)}
									<Button variant="outline" size="sm" onClick={() => openEditor(widget)}>
										<Pencil className="mr-1 h-4 w-4" /> Edit
									</Button>
									<Button variant="outline" size="icon" aria-label={`Delete ${widget.name}`} onClick={() => setDeleting(widget)}>
										<Trash2 className="h-4 w-4" />
									</Button>
								</div>
							</CardContent>
						</Card>
					))}
				</div>
			)}

			<Dialog open={editing !== null} onOpenChange={(open) => !open && closeEditor()}>
				<DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-xl">
					<DialogHeader>
						<DialogTitle>{savedWidget ? `Edit ${savedWidget.name}` : "New widget"}</DialogTitle>
						<DialogDescription>Changes reach your website on the next page load; the install code stays the same.</DialogDescription>
					</DialogHeader>
					{form && (
						<Tabs value={tab} onValueChange={setTab}>
							<TabsList className="grid w-full grid-cols-3">
								<TabsTrigger value="general">General</TabsTrigger>
								<TabsTrigger value="appearance">Appearance</TabsTrigger>
								<TabsTrigger value="install">Install</TabsTrigger>
							</TabsList>

							<TabsContent value="general" className="space-y-4 pt-2">
								<div className="space-y-1">
									<Label htmlFor="widget-name">Name</Label>
									<Input id="widget-name" value={form.name} placeholder="Main website" onChange={(e) => setForm({ ...form, name: e.target.value })} />
								</div>
								<div className="space-y-1">
									<Label>Agent memory</Label>
									<Select value={form.knowledge_base_id} onValueChange={(value) => setForm({ ...form, knowledge_base_id: value })}>
										<SelectTrigger>
											<SelectValue placeholder="Choose an agent memory" />
										</SelectTrigger>
										<SelectContent>
											{knowledgeBases.map((kb) => (
												<SelectItem key={kb.id} value={kb.id}>
													{kbOptionLabel(kb)}
												</SelectItem>
											))}
										</SelectContent>
									</Select>
									{selectedKb && selectedKb.document_count === 0 && (
										<p className="text-xs text-destructive">This agent memory has no documents yet, so answers will be generic.</p>
									)}
								</div>
								<div className="space-y-1">
									<Label htmlFor="widget-origins">Allowed websites</Label>
									<Textarea
										id="widget-origins"
										rows={3}
										value={form.origins}
										placeholder={"https://www.yourshop.com\nhttps://*.yourshop.com"}
										onChange={(e) => setForm({ ...form, origins: e.target.value })}
									/>
									<p className="text-xs text-muted-foreground">
										One per line. The browser refuses to show the widget anywhere else. Use *. for all subdomains.
									</p>
								</div>
								<label className="flex items-center gap-2 text-sm">
									<input type="checkbox" checked={form.is_active} onChange={(e) => setForm({ ...form, is_active: e.target.checked })} />
									Active (disabled widgets disappear from your site and end visitor sessions)
								</label>
								<p className="text-xs text-muted-foreground">
									For safety, website visitors can&apos;t trigger API calls from your instructions (orders, bookings).
								</p>
							</TabsContent>

							<TabsContent value="appearance" className="space-y-4 pt-2">
								<div className="space-y-1">
									<Label htmlFor="widget-title">Title</Label>
									<Input id="widget-title" maxLength={60} value={form.appearance.title} onChange={(e) => setAppearance({ title: e.target.value })} />
								</div>
								<div className="space-y-1">
									<Label htmlFor="widget-welcome">Welcome message</Label>
									<Textarea
										id="widget-welcome"
										rows={2}
										maxLength={500}
										value={form.appearance.welcome_message}
										onChange={(e) => setAppearance({ welcome_message: e.target.value })}
									/>
								</div>
								<div className="flex flex-wrap gap-4">
									<div className="space-y-1">
										<Label htmlFor="widget-color">Color</Label>
										<Input
											id="widget-color"
											type="color"
											className="h-9 w-20 p-1"
											value={form.appearance.primary_color}
											onChange={(e) => setAppearance({ primary_color: e.target.value })}
										/>
									</div>
									<div className="space-y-1">
										<Label>Position</Label>
										<Select value={form.appearance.position} onValueChange={(value) => setAppearance({ position: value as "left" | "right" })}>
											<SelectTrigger className="w-40">
												<SelectValue />
											</SelectTrigger>
											<SelectContent>
												<SelectItem value="right">Bottom right</SelectItem>
												<SelectItem value="left">Bottom left</SelectItem>
											</SelectContent>
										</Select>
									</div>
								</div>
								<div className="space-y-1">
									<Label htmlFor="widget-privacy">Your privacy policy URL</Label>
									<Input
										id="widget-privacy"
										type="url"
										maxLength={500}
										placeholder="https://your-site.com/privacy"
										value={form.appearance.privacy_url}
										onChange={(e) => setAppearance({ privacy_url: e.target.value })}
									/>
									<p className="text-xs text-muted-foreground">
										Linked under the chat next to the AI notice. Leave empty to link our privacy policy.
									</p>
								</div>
								<label className="flex items-center gap-2 text-sm">
									<input
										type="checkbox"
										className="h-4 w-4 accent-primary"
										checked={form.appearance.show_branding}
										onChange={(e) => setAppearance({ show_branding: e.target.checked })}
									/>
									Show &quot;Powered by&quot; link
								</label>
								<p className="text-xs text-muted-foreground">
									The widget always tells visitors they are chatting with an AI assistant (required by the EU AI Act).
								</p>
							</TabsContent>

							<TabsContent value="install" className="space-y-4 pt-2 text-sm">
								{savedWidget ? (
									<>
										<CopyField label="Install code: paste before </body> on every page" value={savedWidget.snippet} />
										<ul className="list-disc space-y-1 pl-5 text-muted-foreground">
											<li>WordPress: a header/footer code plugin, footer section.</li>
											<li>Shopify: Online Store → Themes → Edit code → theme.liquid, before &lt;/body&gt;.</li>
											<li>Webflow / Wix / Squarespace: site settings → custom code (footer).</li>
											<li>React / Next.js and other single-page apps: add it once in the root layout.</li>
										</ul>
										<p className="text-xs text-muted-foreground">
											If your site sends a Content-Security-Policy header, allow {backendOrigin} in script-src and frame-src.
										</p>
										{savedWidget.allowed_origins.length === 0 && (
											<p className="text-xs text-destructive">Add your website under General first, or the widget won&apos;t appear.</p>
										)}
									</>
								) : (
									<p className="text-muted-foreground">Save the widget to get its install code.</p>
								)}
							</TabsContent>
						</Tabs>
					)}
					{formError && <p className="text-sm text-destructive">{formError}</p>}
					<DialogFooter>
						<Button variant="outline" onClick={closeEditor}>
							Close
						</Button>
						<Button onClick={save} disabled={saving}>
							{saving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
							{savedWidget ? "Save" : "Create widget"}
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>

			<Dialog open={deleting !== null} onOpenChange={(open) => !open && setDeleting(null)}>
				<DialogContent>
					<DialogHeader>
						<DialogTitle>Delete {deleting?.name}?</DialogTitle>
						<DialogDescription>
							The widget disappears from your website. Past visitor conversations stay in Conversations.
						</DialogDescription>
					</DialogHeader>
					<DialogFooter>
						<Button variant="outline" onClick={() => setDeleting(null)}>
							Cancel
						</Button>
						<Button variant="destructive" onClick={remove}>
							Delete
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>
		</div>
	);
}
