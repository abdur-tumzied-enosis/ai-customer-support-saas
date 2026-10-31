"use client";

import { useState, useEffect } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Facebook, Instagram, MessageCircle, CheckCircle2, XCircle, Loader2, Plus, Pencil, Trash2, AlertCircle, Copy, Check, ExternalLink, RefreshCw } from "lucide-react";
import { channelApi, featuresApi, platformApi, knowledgeBaseApi, type ChannelCredential, type KnowledgeBase, type Platform } from "@/lib/api";
import { kbOptionLabel } from "@/lib/knowledgeBase";
import { useAuth } from "@/hooks/useAuth";
import WebhookSettings from "@/components/channels/WebhookSettings";
import CopyField from "@/components/channels/CopyField";
import { formatError } from "@/lib/errors";

const PLATFORM_ICONS: Record<string, React.ReactNode> = {
	facebook: <Facebook className="h-5 w-5 text-blue-600" />,
	instagram: <Instagram className="h-5 w-5 text-pink-600" />,
	whatsapp: <MessageCircle className="h-5 w-5 text-green-600" />,
};

// Where the callback URL and verify token go in the Meta App Dashboard
const WEBHOOK_SETUP_WHERE: Record<string, string> = {
	facebook: "Meta App Dashboard → Messenger → Settings → Webhooks",
	instagram: "Meta App Dashboard → Messenger → Instagram settings → Webhooks",
	whatsapp: "Meta App Dashboard → WhatsApp → Configuration → Webhook",
};

const VERIFY_TOKEN = "verify_token";

const newVerifyToken = () => crypto.randomUUID().replace(/-/g, "");

export default function ChannelsPage() {
	const { projects } = useAuth();
	const [channels, setChannels] = useState<ChannelCredential[]>([]);
	const [platforms, setPlatforms] = useState<Platform[]>([]);
	const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
	const [loading, setLoading] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const [success, setSuccess] = useState<string | null>(null);
	
	// Dialog states
	const [addDialogOpen, setAddDialogOpen] = useState(false);
	const [editDialogOpen, setEditDialogOpen] = useState(false);
	const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
	
	// Form states
	const [selectedPlatform, setSelectedPlatform] = useState<Platform | null>(null);
	const [editingChannel, setEditingChannel] = useState<ChannelCredential | null>(null);
	const [deletingChannel, setDeletingChannel] = useState<ChannelCredential | null>(null);
	const [formData, setFormData] = useState<{
		name: string;
		credentials: Record<string, string>;
		knowledge_base_id: string;
	}>({
		name: "",
		credentials: {},
		knowledge_base_id: "",
	});

	const projectSlug = projects.length > 0 ? projects[0].slug : "";
	const projectId = projects.length > 0 ? String(projects[0].id) : "";

	const [imageSearchOn, setImageSearchOn] = useState(false);
	useEffect(() => {
		if (!projectId) return;
		featuresApi
			.get(projectId)
			.then((f) => setImageSearchOn(f.image_search))
			.catch(() => setImageSearchOn(false));
	}, [projectId]);

	const toggleImageSearch = async (channel: ChannelCredential) => {
		try {
			await channelApi.update(channel.id, { image_search_enabled: !channel.image_search_enabled });
			fetchChannels();
		} catch (err) {
			setError(formatError(err));
		}
	};

	// Fetch platforms, channels, and knowledge bases
	useEffect(() => {
		if (projectSlug && projectId) {
			fetchPlatforms();
			fetchChannels();
			fetchKnowledgeBases();
		}
	}, [projectSlug, projectId]);

	const fetchPlatforms = async () => {
		try {
			const response = await platformApi.list();
			setPlatforms(response.data.platforms);
		} catch (err) {
			console.error("Failed to fetch platforms:", err);
		}
	};

	const fetchChannels = async () => {
		if (!projectSlug) return;
		
		setLoading(true);
		try {
			const response = await channelApi.list(projectSlug);
			setChannels(response.data.channels);
		} catch (err) {
			setError(formatError(err));
		} finally {
			setLoading(false);
		}
	};

	const fetchKnowledgeBases = async () => {
		if (!projectId) return;
		
		try {
			const response = await knowledgeBaseApi.list(projectId);
			setKnowledgeBases(response.data.knowledge_bases);
		} catch (err) {
			console.error("Failed to fetch knowledge bases:", err);
		}
	};

	const handleAddChannel = async () => {
		if (!selectedPlatform) {
			setError("Please select a platform");
			return;
		}

		if (!formData.name.trim()) {
			setError("Please enter a name for this channel");
			return;
		}

		// Validate required credentials
		const missingFields = selectedPlatform.credential_schema
			.filter(field => field.required && !formData.credentials[field.name]?.trim())
			.map(field => field.label);

		if (missingFields.length > 0) {
			setError(`Please fill in required fields: ${missingFields.join(", ")}`);
			return;
		}

		// The backend ignores incoming messages on channels without a knowledge base
		if (!formData.knowledge_base_id) {
			setError("Please select a knowledge base");
			return;
		}

		setLoading(true);
		setError(null);

		try {
			await channelApi.create({
				project_slug: projectSlug,
				name: formData.name,
				channel: selectedPlatform.name,
				credentials: formData.credentials,
				knowledge_base_id: formData.knowledge_base_id || null,
			});

			setSuccess("Channel added successfully!");
			setAddDialogOpen(false);
			resetForm();
			fetchChannels();
		} catch (err) {
			setError(formatError(err));
		} finally {
			setLoading(false);
		}
	};

	const handleEditChannel = async () => {
		if (!editingChannel) return;

		setLoading(true);
		setError(null);

		// Empty fields mean "keep current value", so they are not sent
		const changedCredentials = Object.fromEntries(
			Object.entries(formData.credentials).filter(([, value]) => value.trim())
		);

		try {
			await channelApi.update(editingChannel.id, {
				name: formData.name || undefined,
				credentials: Object.keys(changedCredentials).length > 0 ? changedCredentials : undefined,
				knowledge_base_id: formData.knowledge_base_id || null,
			});

			setSuccess("Channel updated successfully!");
			setEditDialogOpen(false);
			setEditingChannel(null);
			resetForm();
			fetchChannels();
		} catch (err) {
			setError(formatError(err));
		} finally {
			setLoading(false);
		}
	};

	const handleDeleteChannel = async () => {
		if (!deletingChannel) return;

		setLoading(true);
		setError(null);

		try {
			await channelApi.delete(deletingChannel.id);
			setSuccess("Channel deleted successfully!");
			setDeleteDialogOpen(false);
			setDeletingChannel(null);
			fetchChannels();
		} catch (err) {
			setError(formatError(err));
		} finally {
			setLoading(false);
		}
	};

	const resetForm = () => {
		setFormData({
			name: "",
			credentials: {},
			knowledge_base_id: "",
		});
		setSelectedPlatform(null);
	};

	const openAddDialog = () => {
		resetForm();
		const defaultKb = knowledgeBases.find((kb) => kb.is_default);
		if (defaultKb) setFormData((prev) => ({ ...prev, knowledge_base_id: defaultKb.id }));
		setAddDialogOpen(true);
	};

	const openEditDialog = (channel: ChannelCredential) => {
		setEditingChannel(channel);
		setFormData({
			name: channel.name,
			credentials: {},
			knowledge_base_id: channel.knowledge_base?.id || "",
		});
		
		const platform = platforms.find(p => p.name === channel.channel);
		setSelectedPlatform(platform || null);
		setEditDialogOpen(true);
	};

	const openDeleteDialog = (channel: ChannelCredential) => {
		setDeletingChannel(channel);
		setDeleteDialogOpen(true);
	};

	const handlePlatformSelect = (platformName: string) => {
		const platform = platforms.find(p => p.name === platformName);
		setSelectedPlatform(platform || null);
		// Initialize credentials object with empty values
		if (platform) {
			const initialCreds: Record<string, string> = {};
			platform.credential_schema.forEach(field => {
				initialCreds[field.name] = field.name === VERIFY_TOKEN ? newVerifyToken() : "";
			});
			setFormData(prev => ({ ...prev, credentials: initialCreds }));
		}
	};

	const setCredential = (name: string, value: string) =>
		setFormData(prev => ({ ...prev, credentials: { ...prev.credentials, [name]: value } }));

	const renderKnowledgeBaseSelect = (id: string, isEdit: boolean = false) => (
		<div className="space-y-2">
			<Label htmlFor={id}>
				Knowledge Base <span className="text-destructive">*</span>
			</Label>
			<Select
				value={formData.knowledge_base_id || undefined}
				onValueChange={(value: string) => setFormData(prev => ({ ...prev, knowledge_base_id: value }))}
			>
				<SelectTrigger id={id}>
					<SelectValue placeholder="Select a knowledge base" />
				</SelectTrigger>
				<SelectContent>
					{knowledgeBases.map((kb) => (
						<SelectItem key={kb.id} value={kb.id}>
							{kbOptionLabel(kb)}
						</SelectItem>
					))}
				</SelectContent>
			</Select>
			<p className="text-xs text-muted-foreground">
				{knowledgeBases.length === 0
					? "No knowledge bases yet. Create one first; the AI answers from it."
					: isEdit
						? "Changing this applies to existing customers from their next message"
						: "The AI answers this channel's messages from this knowledge base"}
			</p>
		</div>
	);

	const renderWebhookSetup = (platform: Platform, isEdit: boolean = false) => {
		const hasVerifyToken = platform.credential_schema.some(field => field.name === VERIFY_TOKEN);
		return (
			<div className="rounded-md border bg-muted/40 p-4 space-y-3">
				<div>
					<p className="text-sm font-medium">Webhook setup</p>
					<p className="text-xs text-muted-foreground mt-1">
						Paste these in {WEBHOOK_SETUP_WHERE[platform.name] || "the Meta App Dashboard"}, then subscribe to
						the <code className="font-mono">messages</code> field.
					</p>
				</div>
				<CopyField label="Callback URL" value={platform.webhook_url} />
				{hasVerifyToken && (
					<div className="space-y-1">
						<Label htmlFor="verify-token" className="text-xs text-muted-foreground">Verify token</Label>
						<div className="flex gap-2">
							<Input
								id="verify-token"
								className="font-mono text-xs"
								placeholder={isEdit ? "Leave empty to keep current value" : ""}
								value={formData.credentials[VERIFY_TOKEN] || ""}
								onChange={(e) => setCredential(VERIFY_TOKEN, e.target.value)}
							/>
							<Button
								type="button"
								variant="outline"
								size="icon"
								aria-label="Generate verify token"
								onClick={() => setCredential(VERIFY_TOKEN, newVerifyToken())}
							>
								<RefreshCw className="h-4 w-4" />
							</Button>
							<Button
								type="button"
								variant="outline"
								size="icon"
								aria-label="Copy verify token"
								disabled={!formData.credentials[VERIFY_TOKEN]}
								onClick={() => navigator.clipboard?.writeText(formData.credentials[VERIFY_TOKEN])}
							>
								<Copy className="h-4 w-4" />
							</Button>
						</div>
						{isEdit && formData.credentials[VERIFY_TOKEN] && (
							<p className="text-xs text-muted-foreground">Update it in the Meta dashboard too, or verification will fail.</p>
						)}
					</div>
				)}
				{platform.documentation_url && (
					<a
						href={platform.documentation_url}
						target="_blank"
						rel="noreferrer"
						className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
					>
						{platform.display_name} docs <ExternalLink className="h-3 w-3" />
					</a>
				)}
			</div>
		);
	};

	const renderCredentialFields = (platform: Platform, isEdit: boolean = false) => {
		return platform.credential_schema.filter((field) => field.name !== VERIFY_TOKEN).map((field) => (
			<div key={field.name} className="space-y-2">
				<Label htmlFor={field.name}>
					{field.label}
					{field.required && <span className="text-destructive ml-1">*</span>}
				</Label>
				<Input
					id={field.name}
					type={field.type === "password" ? "password" : "text"}
					placeholder={isEdit ? "Leave empty to keep current value" : field.placeholder}
					value={formData.credentials[field.name] || ""}
					onChange={(e) =>
						setFormData(prev => ({
							...prev,
							credentials: { ...prev.credentials, [field.name]: e.target.value },
						}))
					}
					required={!isEdit && field.required}
				/>
				{field.description && (
					<p className="text-xs text-muted-foreground">{field.description}</p>
				)}
			</div>
		));
	};

	return (
		<div className="space-y-6">
			{/* Header */}
			<div className="flex items-center justify-between">
				<div>
					<h1 className="text-2xl font-semibold">Channels</h1>
					<p className="text-sm text-muted-foreground mt-1">
						Connect messaging platforms to receive and manage customer conversations
					</p>
				</div>
				<Button onClick={openAddDialog}>
					<Plus className="mr-2 h-4 w-4" />
					Add Channel
				</Button>
			</div>

			{/* Alerts */}
			{error && (
				<Alert variant="destructive">
					<AlertCircle className="h-4 w-4" />
					<AlertDescription>{error}</AlertDescription>
				</Alert>
			)}

			{success && (
				<Alert>
					<CheckCircle2 className="h-4 w-4" />
					<AlertDescription>{success}</AlertDescription>
				</Alert>
			)}

			{/* Loading State */}
			{loading && channels.length === 0 && (
				<div className="flex items-center justify-center py-12">
					<Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
				</div>
			)}

			{/* Channels List */}
			{!loading && channels.length === 0 && (
				<Card>
					<CardContent className="flex flex-col items-center justify-center py-12">
						<MessageCircle className="h-12 w-12 text-muted-foreground mb-4" />
						<p className="text-muted-foreground mb-4">No channels connected yet</p>
						<Button onClick={openAddDialog}>
							<Plus className="mr-2 h-4 w-4" />
							Add Your First Channel
						</Button>
					</CardContent>
				</Card>
			)}

			{channels.length > 0 && (
				<div className="grid gap-4 md:grid-cols-2">
					{channels.map((channel) => (
						<Card key={channel.id}>
							<CardHeader>
								<div className="flex items-start justify-between">
									<div className="flex items-start gap-3">
										<div className="mt-1">
											{channel.platform && PLATFORM_ICONS[channel.platform.name]}
										</div>
										<div>
											<CardTitle className="text-lg">{channel.name}</CardTitle>
											<CardDescription className="mt-1">
												{channel.platform?.display_name || channel.channel}
											</CardDescription>
										</div>
									</div>
									<div className="flex gap-1">
										<Button
											variant="ghost"
											size="icon"
											onClick={() => openEditDialog(channel)}
										>
											<Pencil className="h-4 w-4" />
										</Button>
										<Button
											variant="ghost"
											size="icon"
											onClick={() => openDeleteDialog(channel)}
										>
											<Trash2 className="h-4 w-4" />
										</Button>
									</div>
								</div>
							</CardHeader>
							<CardContent className="space-y-3">
								<div className="flex items-center gap-2">
									{channel.is_configured ? (
										<Badge variant="default" className="bg-green-600">
											<CheckCircle2 className="mr-1 h-3 w-3" />
											Configured
										</Badge>
									) : (
										<Badge variant="secondary">
											<XCircle className="mr-1 h-3 w-3" />
											Not Configured
										</Badge>
									)}
									{!channel.is_active && (
										<Badge variant="outline">Inactive</Badge>
									)}
									{imageSearchOn && projects[0]?.role !== "admin" && (
										<span className="ml-auto text-xs text-muted-foreground">
											Image search: {channel.image_search_enabled ? "On" : "Off"}
										</span>
									)}
									{imageSearchOn && projects[0]?.role === "admin" && (
										<Button
											variant={channel.image_search_enabled ? "default" : "outline"}
											size="sm"
											className="ml-auto"
											onClick={() => toggleImageSearch(channel)}
										>
											Image search: {channel.image_search_enabled ? "On" : "Off"}
										</Button>
									)}
									{!channel.knowledge_base && (
										<Badge variant="destructive">
											<AlertCircle className="mr-1 h-3 w-3" />
											No knowledge base — messages are ignored
										</Badge>
									)}
								</div>

								{channel.knowledge_base && (
									<div className="pt-3 border-t">
										<p className="text-xs text-muted-foreground">Knowledge Base:</p>
										<p className="text-sm font-medium">{channel.knowledge_base.name}</p>
									</div>
								)}
							</CardContent>
						</Card>
					))}
				</div>
			)}

			{/* Add Channel Dialog */}
			<Dialog open={addDialogOpen} onOpenChange={(open) => {
				setAddDialogOpen(open);
				if (!open) resetForm();
			}}>
				<DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
					<DialogHeader>
						<DialogTitle>Add Channel</DialogTitle>
						<DialogDescription>
							Connect a new messaging platform to your project
						</DialogDescription>
					</DialogHeader>

					<div className="space-y-4 py-4">
						{/* Platform Selection */}
						{!selectedPlatform && (
							<div className="space-y-3">
								<Label>Select Platform</Label>
								<div className="grid grid-cols-2 gap-3">
									{platforms.filter(p => p.is_active).map((platform) => (
										<Card
											key={platform.id}
											className="cursor-pointer hover:border-primary transition-colors"
											onClick={() => handlePlatformSelect(platform.name)}
										>
											<CardContent className="flex items-center gap-3 p-4">
												{PLATFORM_ICONS[platform.name]}
												<div>
													<p className="font-medium">{platform.display_name}</p>
													<p className="text-xs text-muted-foreground line-clamp-2">
														{platform.description}
													</p>
												</div>
											</CardContent>
										</Card>
									))}
								</div>
							</div>
						)}

						{/* Form Fields */}
						{selectedPlatform && (
							<>
								<div className="flex items-center justify-between pb-2 border-b">
									<div className="flex items-center gap-2">
										{PLATFORM_ICONS[selectedPlatform.name]}
										<span className="font-medium">{selectedPlatform.display_name}</span>
									</div>
									<Button
										variant="ghost"
										size="sm"
										onClick={() => setSelectedPlatform(null)}
									>
										Change Platform
									</Button>
								</div>

								<div className="space-y-2">
									<Label htmlFor="channel-name">
										Channel Name <span className="text-destructive">*</span>
									</Label>
									<Input
										id="channel-name"
										placeholder={`e.g., Main ${selectedPlatform.display_name}`}
										value={formData.name}
										onChange={(e) => setFormData(prev => ({ ...prev, name: e.target.value }))}
										required
									/>
								</div>

								{renderCredentialFields(selectedPlatform)}

								{renderKnowledgeBaseSelect("kb-select")}

								{renderWebhookSetup(selectedPlatform)}
							</>
						)}
					</div>

					<DialogFooter>
						<Button
							variant="outline"
							onClick={() => {
								setAddDialogOpen(false);
								resetForm();
							}}
							disabled={loading}
						>
							Cancel
						</Button>
						<Button
							onClick={handleAddChannel}
							disabled={loading || !selectedPlatform}
						>
							{loading ? (
								<>
									<Loader2 className="mr-2 h-4 w-4 animate-spin" />
									Adding...
								</>
							) : (
								"Add Channel"
							)}
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>

			{/* Edit Channel Dialog */}
			<Dialog open={editDialogOpen} onOpenChange={(open) => {
				setEditDialogOpen(open);
				if (!open) {
					setEditingChannel(null);
					resetForm();
				}
			}}>
				<DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
					<DialogHeader>
						<DialogTitle>Edit Channel</DialogTitle>
						<DialogDescription>
							Update channel configuration. Leave credential fields empty to keep current values.
						</DialogDescription>
					</DialogHeader>

					<div className="space-y-4 py-4">
						{selectedPlatform && (
							<>
								<div className="flex items-center gap-2 pb-2 border-b">
									{PLATFORM_ICONS[selectedPlatform.name]}
									<span className="font-medium">{selectedPlatform.display_name}</span>
								</div>

								<div className="space-y-2">
									<Label htmlFor="edit-channel-name">Channel Name</Label>
									<Input
										id="edit-channel-name"
										placeholder="Channel name"
										value={formData.name}
										onChange={(e) => setFormData(prev => ({ ...prev, name: e.target.value }))}
									/>
								</div>

								{renderCredentialFields(selectedPlatform, true)}

								{renderKnowledgeBaseSelect("edit-kb-select", true)}

								{renderWebhookSetup(selectedPlatform, true)}
							</>
						)}
					</div>

					<DialogFooter>
						<Button
							variant="outline"
							onClick={() => {
								setEditDialogOpen(false);
								setEditingChannel(null);
								resetForm();
							}}
							disabled={loading}
						>
							Cancel
						</Button>
						<Button onClick={handleEditChannel} disabled={loading}>
							{loading ? (
								<>
									<Loader2 className="mr-2 h-4 w-4 animate-spin" />
									Updating...
								</>
							) : (
								"Update Channel"
							)}
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>

			{/* Delete Confirmation Dialog */}
			<Dialog open={deleteDialogOpen} onOpenChange={(open) => {
				setDeleteDialogOpen(open);
				if (!open) setDeletingChannel(null);
			}}>
				<DialogContent>
					<DialogHeader>
						<DialogTitle>Delete Channel</DialogTitle>
						<DialogDescription>
							Are you sure you want to delete this channel? This action cannot be undone.
						</DialogDescription>
					</DialogHeader>

					{deletingChannel && (
						<div className="py-4">
							<div className="rounded-md border p-3 bg-muted/50">
								<div className="flex items-center gap-2">
									{deletingChannel.platform && PLATFORM_ICONS[deletingChannel.platform.name]}
									<div>
										<p className="font-medium">{deletingChannel.name}</p>
										<p className="text-sm text-muted-foreground">
											{deletingChannel.platform?.display_name}
										</p>
									</div>
								</div>
							</div>
						</div>
					)}

					<DialogFooter>
						<Button
							variant="outline"
							onClick={() => {
								setDeleteDialogOpen(false);
								setDeletingChannel(null);
							}}
							disabled={loading}
						>
							Cancel
						</Button>
						<Button
							variant="destructive"
							onClick={handleDeleteChannel}
							disabled={loading}
						>
							{loading ? (
								<>
									<Loader2 className="mr-2 h-4 w-4 animate-spin" />
									Deleting...
								</>
							) : (
								"Delete"
							)}
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>
			{projectSlug && <WebhookSettings projectSlug={projectSlug} />}
		</div>
	);
}
