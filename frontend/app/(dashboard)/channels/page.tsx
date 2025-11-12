"use client";

import { useState, useEffect } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger, DialogFooter } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Separator } from "@/components/ui/separator";
import { Facebook, MessageCircle, CheckCircle2, XCircle, Loader2, ExternalLink, RefreshCw, Settings, Info } from "lucide-react";
import { channelApi } from "@/lib/api";
import { useAuth } from "@/hooks/useAuth";
import { formatError } from "@/lib/errors";

type ChannelStatus = {
	name: string;
	icon: React.ReactNode;
	connected: boolean;
	description: string;
	color: string;
};

export default function ChannelsPage() {
	const { projects } = useAuth();
	const [loading, setLoading] = useState<string | null>(null);
	const [fetchingCredentials, setFetchingCredentials] = useState(true);
	const [error, setError] = useState<string | null>(null);
	const [success, setSuccess] = useState<string | null>(null);
	const [facebookDialogOpen, setFacebookDialogOpen] = useState(false);
	const [whatsappDialogOpen, setWhatsappDialogOpen] = useState(false);

	// Form state for Facebook
	const [facebookForm, setFacebookForm] = useState({
		page_id: "",
		page_access_token: "",
		meta_app_id: "",
		meta_app_secret: "",
		verify_token: "",
	});

	// Form state for WhatsApp
	const [whatsappForm, setWhatsappForm] = useState({
		phone_number_id: "",
		access_token: "",
		verify_token: "",
	});

	// Channel connection status from API
	const [channelStatus, setChannelStatus] = useState<Record<string, boolean>>({
		facebook: false,
		whatsapp: false,
	});

	// Store channel details from API
	const [channelDetails, setChannelDetails] = useState<Record<string, { id: string; is_configured: boolean }>>({});

	const getProjectSlug = () => {
		return projects.length > 0 ? projects[0].slug : "";
	};

	// Fetch channel credentials
	const fetchCredentials = async () => {
		const projectSlug = projects.length > 0 ? projects[0].slug : "";
		if (!projectSlug) {
			setFetchingCredentials(false);
			return;
		}

		try {
			setFetchingCredentials(true);
			const response = await channelApi.getCredentials(projectSlug);
			
			// Update channel status and details based on API response
			const status: Record<string, boolean> = {
				facebook: false,
				whatsapp: false,
			};
			const details: Record<string, { id: string; is_configured: boolean }> = {};

			response.data.channels.forEach((channel) => {
				if (channel.channel === "facebook") {
					status.facebook = channel.is_configured;
					details.facebook = { id: channel.id, is_configured: channel.is_configured };
				} else if (channel.channel === "whatsapp") {
					status.whatsapp = channel.is_configured;
					details.whatsapp = { id: channel.id, is_configured: channel.is_configured };
				}
			});

			setChannelStatus(status);
			setChannelDetails(details);
		} catch (err) {
			console.error("Failed to fetch channel credentials:", err);
			setError("Failed to load channel status. Please try again.");
		} finally {
			setFetchingCredentials(false);
		}
	};

	// Fetch channel credentials on mount and when projects change
	useEffect(() => {
		fetchCredentials();
	}, [projects]);

	// Auto-dismiss success message after 5 seconds
	useEffect(() => {
		if (success) {
			const timer = setTimeout(() => {
				setSuccess(null);
			}, 5000);
			return () => clearTimeout(timer);
		}
	}, [success]);

	const channels: ChannelStatus[] = [
		{
			name: "Facebook",
			icon: <Facebook className="h-5 w-5" />,
			connected: channelStatus.facebook,
			description: "Connect your Facebook Page to receive and respond to messages",
			color: "text-blue-600",
		},
		{
			name: "WhatsApp",
			icon: <MessageCircle className="h-5 w-5" />,
			connected: channelStatus.whatsapp,
			description: "Connect WhatsApp Business API to manage customer conversations",
			color: "text-green-600",
		},
	];

	const handleFacebookConnect = async () => {
		const projectSlug = getProjectSlug();
		if (!projectSlug) {
			setError("No project found. Please ensure you have access to a project.");
			return;
		}

		setLoading("facebook");
		setError(null);
		setSuccess(null);

		try {
			await channelApi.configureFacebook({
				project_slug: projectSlug,
				page_id: facebookForm.page_id || undefined,
				page_access_token: facebookForm.page_access_token || undefined,
				meta_app_id: facebookForm.meta_app_id || undefined,
				meta_app_secret: facebookForm.meta_app_secret || undefined,
				verify_token: facebookForm.verify_token || undefined,
			});

			setSuccess("Facebook channel configured successfully!");
			setFacebookDialogOpen(false);
			// Reset form
			setFacebookForm({
				page_id: "",
				page_access_token: "",
				meta_app_id: "",
				meta_app_secret: "",
				verify_token: "",
			});

			// Refresh credentials to update connection status
			await fetchCredentials();
		} catch (err) {
			setError(formatError(err));
		} finally {
			setLoading(null);
		}
	};

	const handleWhatsAppConnect = async () => {
		setLoading("whatsapp");
		setError(null);
		setSuccess(null);

		// TODO: Implement WhatsApp API endpoint when available
		setTimeout(() => {
			setSuccess("WhatsApp connection coming soon!");
			setChannelStatus((prev) => ({ ...prev, whatsapp: true }));
			setWhatsappDialogOpen(false);
			setLoading(null);
		}, 1000);
	};

	return (
		<div className="space-y-6">
			<div className="flex items-center justify-between">
				<div>
					<h1 className="text-2xl font-semibold">Channels</h1>
					<p className="text-sm text-muted-foreground mt-1">
						Connect external channels to receive and manage customer conversations
					</p>
				</div>
				<Button
					variant="outline"
					size="sm"
					onClick={fetchCredentials}
					disabled={fetchingCredentials}
					className="gap-2"
				>
					<RefreshCw className={`h-4 w-4 ${fetchingCredentials ? "animate-spin" : ""}`} />
					Refresh
				</Button>
			</div>

			{error && (
				<Alert variant="destructive">
					<AlertDescription>{error}</AlertDescription>
				</Alert>
			)}

			{success && (
				<Alert>
					<CheckCircle2 className="h-4 w-4" />
					<AlertDescription>{success}</AlertDescription>
				</Alert>
			)}

			{fetchingCredentials && (
				<div className="flex items-center justify-center py-8">
					<Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
					<span className="ml-2 text-sm text-muted-foreground">Loading channel status...</span>
				</div>
			)}

			{!fetchingCredentials && (
				<div className="grid gap-6 md:grid-cols-2">
				{channels.map((channel) => {
					const channelKey = channel.name.toLowerCase();
					const isConnected = channel.connected;
					const details = channelDetails[channelKey];

					return (
					<Card key={channel.name} className={`relative transition-all hover:shadow-lg ${isConnected ? "border-green-500/20" : ""}`}>
						<CardHeader>
							<div className="flex items-start justify-between gap-4">
								<div className="flex items-start gap-3 flex-1">
									<div className={`${channel.color} mt-1`}>{channel.icon}</div>
									<div className="flex-1 min-w-0">
										<div className="flex items-center gap-2 mb-1">
											<CardTitle className="text-lg">{channel.name}</CardTitle>
											{isConnected ? (
												<Badge variant="default" className="bg-green-600 hover:bg-green-700">
													<CheckCircle2 className="h-3 w-3 mr-1" />
													Connected
												</Badge>
											) : (
												<Badge variant="outline" className="border-muted-foreground/30">
													<XCircle className="h-3 w-3 mr-1" />
													Not Connected
												</Badge>
											)}
										</div>
										<CardDescription className="mt-1">{channel.description}</CardDescription>
										{isConnected && details && (
											<div className="mt-3 pt-3 border-t border-border">
												<div className="flex items-center gap-2 text-xs text-muted-foreground">
													<Info className="h-3 w-3" />
													<span>Channel ID: {details.id.slice(0, 8)}...</span>
												</div>
											</div>
										)}
									</div>
								</div>
							</div>
						</CardHeader>
						<CardContent className="space-y-4">
							{channel.name === "Facebook" ? (
								<Dialog open={facebookDialogOpen} onOpenChange={setFacebookDialogOpen}>
									<DialogTrigger asChild>
										<Button 
											variant={channel.connected ? "outline" : "default"} 
											className="w-full gap-2"
										>
											{channel.connected ? (
												<>
													<Settings className="h-4 w-4" />
													Update Configuration
												</>
											) : (
												<>
													<Facebook className="h-4 w-4" />
													Connect Facebook
												</>
											)}
										</Button>
									</DialogTrigger>
									<DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
										<DialogHeader>
											<DialogTitle className="flex items-center gap-2">
												<Facebook className="h-5 w-5 text-blue-600" />
												{isConnected ? "Update Facebook Configuration" : "Connect Facebook"}
											</DialogTitle>
											<DialogDescription>
												{isConnected 
													? "Update your Facebook Page configuration. Leave fields empty to keep existing values."
													: "Configure your Facebook Page to receive messages. You'll need to create a Meta App and get your Page Access Token."
												}
											</DialogDescription>
										</DialogHeader>
										<div className="space-y-4 py-4">
											<div className="space-y-2">
												<Label htmlFor="page_id">Page ID</Label>
												<Input
													id="page_id"
													placeholder="Enter your Facebook Page ID"
													value={facebookForm.page_id}
													onChange={(e) => setFacebookForm({ ...facebookForm, page_id: e.target.value })}
												/>
											</div>
											<div className="space-y-2">
												<Label htmlFor="page_access_token">Page Access Token</Label>
												<Input
													id="page_access_token"
													type="password"
													placeholder="Enter your Page Access Token"
													value={facebookForm.page_access_token}
													onChange={(e) => setFacebookForm({ ...facebookForm, page_access_token: e.target.value })}
												/>
											</div>
											<div className="space-y-2">
												<Label htmlFor="meta_app_id">Meta App ID</Label>
												<Input
													id="meta_app_id"
													placeholder="Enter your Meta App ID"
													value={facebookForm.meta_app_id}
													onChange={(e) => setFacebookForm({ ...facebookForm, meta_app_id: e.target.value })}
												/>
											</div>
											<div className="space-y-2">
												<Label htmlFor="meta_app_secret">Meta App Secret</Label>
												<Input
													id="meta_app_secret"
													type="password"
													placeholder="Enter your Meta App Secret"
													value={facebookForm.meta_app_secret}
													onChange={(e) => setFacebookForm({ ...facebookForm, meta_app_secret: e.target.value })}
												/>
											</div>
											<div className="space-y-2">
												<Label htmlFor="verify_token">Verify Token</Label>
												<Input
													id="verify_token"
													placeholder="Enter your webhook verify token"
													value={facebookForm.verify_token}
													onChange={(e) => setFacebookForm({ ...facebookForm, verify_token: e.target.value })}
												/>
											</div>
											<div className="text-xs text-muted-foreground space-y-1">
												<p>
													<strong>Need help?</strong> Follow these steps:
												</p>
												<ol className="list-decimal list-inside space-y-1 ml-2">
													<li>Create a Meta App at{" "}
														<a href="https://developers.facebook.com/apps" target="_blank" rel="noopener noreferrer" className="text-primary hover:underline inline-flex items-center gap-1">
															developers.facebook.com <ExternalLink className="h-3 w-3" />
														</a>
													</li>
													<li>Add Messenger product to your app</li>
													<li>Get your Page Access Token from the app settings</li>
													<li>Configure webhook URL: <code className="bg-muted px-1 rounded">/webhooks/facebook/</code></li>
												</ol>
											</div>
										</div>
										<DialogFooter>
											<Button variant="outline" onClick={() => setFacebookDialogOpen(false)} disabled={loading === "facebook"}>
												Cancel
											</Button>
											<Button onClick={handleFacebookConnect} disabled={loading === "facebook"}>
												{loading === "facebook" ? (
													<>
														<Loader2 className="mr-2 h-4 w-4 animate-spin" />
														{isConnected ? "Updating..." : "Connecting..."}
													</>
												) : (
													isConnected ? "Update Configuration" : "Connect"
												)}
											</Button>
										</DialogFooter>
									</DialogContent>
								</Dialog>
							) : (
								<Dialog open={whatsappDialogOpen} onOpenChange={setWhatsappDialogOpen}>
									<DialogTrigger asChild>
										<Button 
											variant={isConnected ? "outline" : "default"} 
											className="w-full gap-2"
										>
											{isConnected ? (
												<>
													<Settings className="h-4 w-4" />
													Update Configuration
												</>
											) : (
												<>
													<MessageCircle className="h-4 w-4" />
													Connect WhatsApp
												</>
											)}
										</Button>
									</DialogTrigger>
									<DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
										<DialogHeader>
											<DialogTitle className="flex items-center gap-2">
												<MessageCircle className="h-5 w-5 text-green-600" />
												{isConnected ? "Update WhatsApp Configuration" : "Connect WhatsApp"}
											</DialogTitle>
											<DialogDescription>
												{isConnected
													? "Update your WhatsApp Business API configuration. Leave fields empty to keep existing values."
													: "Configure WhatsApp Business API to receive and send messages. You'll need a Meta Business Account and WhatsApp Business API access."
												}
											</DialogDescription>
										</DialogHeader>
										<div className="space-y-4 py-4">
											<div className="space-y-2">
												<Label htmlFor="whatsapp_phone_number_id">Phone Number ID</Label>
												<Input
													id="whatsapp_phone_number_id"
													placeholder="Enter your WhatsApp Phone Number ID"
													value={whatsappForm.phone_number_id}
													onChange={(e) => setWhatsappForm({ ...whatsappForm, phone_number_id: e.target.value })}
												/>
											</div>
											<div className="space-y-2">
												<Label htmlFor="whatsapp_access_token">Access Token</Label>
												<Input
													id="whatsapp_access_token"
													type="password"
													placeholder="Enter your WhatsApp Access Token"
													value={whatsappForm.access_token}
													onChange={(e) => setWhatsappForm({ ...whatsappForm, access_token: e.target.value })}
												/>
											</div>
											<div className="space-y-2">
												<Label htmlFor="whatsapp_verify_token">Verify Token</Label>
												<Input
													id="whatsapp_verify_token"
													placeholder="Enter your webhook verify token"
													value={whatsappForm.verify_token}
													onChange={(e) => setWhatsappForm({ ...whatsappForm, verify_token: e.target.value })}
												/>
											</div>
											<div className="text-xs text-muted-foreground space-y-1">
												<p>
													<strong>Note:</strong> WhatsApp Business API integration is coming soon. Make sure you have:
												</p>
												<ul className="list-disc list-inside space-y-1 ml-2">
													<li>A Meta Business Account</li>
													<li>WhatsApp Business API access</li>
													<li>Phone number registered with WhatsApp Business API</li>
												</ul>
											</div>
										</div>
										<DialogFooter>
											<Button variant="outline" onClick={() => setWhatsappDialogOpen(false)}>
												Cancel
											</Button>
											<Button onClick={handleWhatsAppConnect} disabled={loading === "whatsapp"}>
												{loading === "whatsapp" ? (
													<>
														<Loader2 className="mr-2 h-4 w-4 animate-spin" />
														Connecting...
													</>
												) : (
													"Connect"
												)}
											</Button>
										</DialogFooter>
									</DialogContent>
								</Dialog>
							)}
						</CardContent>
					</Card>
					);
				})}
				</div>
			)}
		</div>
	);
}
