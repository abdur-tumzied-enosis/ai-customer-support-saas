"use client";

import { useState } from "react";
import Script from "next/script";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { useToast } from "@/components/ui/toast";
import { channelApi, type MetaPageChoice } from "@/lib/api";
import { formatError } from "@/lib/errors";

// NEXT_PUBLIC_* is inlined at build time, so the static property access must stay as written
const META_APP_ID = process.env.NEXT_PUBLIC_META_APP_ID || "";
const GRAPH_VERSION = "v21.0";
const META_SCOPES = "pages_show_list,pages_manage_metadata,pages_messaging,pages_read_engagement,instagram_basic,instagram_manage_messages";

type Props = {
	projectSlug: string;
	onConnected: () => void;
};

export default function MetaConnectButton({ projectSlug, onConnected }: Props) {
	const toast = useToast();
	const [connecting, setConnecting] = useState(false);
	// The long-lived user token is never kept here: it goes straight to the backend
	const [setup, setSetup] = useState<{ setupId: string; pages: MetaPageChoice[] } | null>(null);
	const [picking, setPicking] = useState(false);

	const exchange = async (token: string) => {
		try {
			const response = await channelApi.exchangeMetaToken({ project_slug: projectSlug, access_token: token });
			const result = response.data;
			if (result.connected) {
				const names = (result.channels ?? []).map((c) => c.name).join(", ");
				toast.success(`Connected: ${names}`);
				onConnected();
			} else if (result.setup_id && result.pages) {
				setSetup({ setupId: result.setup_id, pages: result.pages });
			}
		} catch (err) {
			toast.error(formatError(err));
		} finally {
			setConnecting(false);
		}
	};

	const handleLogin = () => {
		if (!window.FB) {
			toast.error("Facebook is still loading. Try again in a moment.");
			return;
		}
		setConnecting(true);
		window.FB.login((response) => {
			const token = response.authResponse?.accessToken;
			if (!token) {
				setConnecting(false);
				toast.error("Facebook login was cancelled. Try again to connect.");
				return;
			}
			const granted = (response.authResponse?.grantedScopes ?? "").split(",");
			if (META_SCOPES.split(",").some((scope) => !granted.includes(scope))) {
				setConnecting(false);
				toast.error("Please approve all requested permissions");
				return;
			}
			void exchange(token);
		}, { scope: META_SCOPES, return_scopes: true });
	};

	const choosePage = async (page: MetaPageChoice) => {
		if (!setup) return;
		setPicking(true);
		try {
			await channelApi.connectMetaPage({ project_slug: projectSlug, setup_id: setup.setupId, page_id: page.id });
			setSetup(null);
			toast.success(`Connected ${page.name}`);
			onConnected();
		} catch (err) {
			toast.error(formatError(err));
		} finally {
			setPicking(false);
		}
	};

	return (
		<div className="flex flex-col gap-2">
			<Script
				id="meta-fb-sdk"
				strategy="afterInteractive"
				src="https://connect.facebook.net/en_US/sdk.js"
				onReady={() => {
					// onReady, not onLoad: onLoad fires once, so returning to this page would skip init
					if (META_APP_ID) {
						window.FB?.init({ appId: META_APP_ID, cookie: true, xfbml: false, version: GRAPH_VERSION });
					}
				}}
			/>

			{META_APP_ID ? (
				<Button onClick={handleLogin} disabled={!projectSlug || connecting} className="w-fit">
					{connecting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
					Connect to Facebook Messenger
				</Button>
			) : (
				<p className="text-sm text-muted-foreground">Meta connect is not configured.</p>
			)}
			<p className="text-sm text-muted-foreground">
				Instagram DMs are connected too when your Page has a linked Instagram account.
			</p>

			<Dialog open={setup !== null} onOpenChange={(open) => { if (!open) setSetup(null); }}>
				<DialogContent>
					<DialogHeader>
						<DialogTitle>Which Page should we connect?</DialogTitle>
						<DialogDescription>Pick the Facebook Page whose messages should reach your agent.</DialogDescription>
					</DialogHeader>
					<div className="flex flex-col gap-2">
						{setup?.pages.map((page) => (
							<Button
								key={page.id}
								variant="outline"
								className="justify-start"
								disabled={picking}
								onClick={() => choosePage(page)}
							>
								{page.name}
								{page.instagram_username && (
									<span className="ml-2 text-muted-foreground">@{page.instagram_username}</span>
								)}
							</Button>
						))}
					</div>
				</DialogContent>
			</Dialog>
		</div>
	);
}
