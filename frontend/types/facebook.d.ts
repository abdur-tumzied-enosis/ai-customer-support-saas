// Minimal ambient typing for the Facebook JavaScript SDK (window.FB), loaded in MetaConnectButton.
// The export {} makes this a module so that declare global is allowed; nothing is exported.

type FBLoginResponse = {
	authResponse?: {
		accessToken?: string;
		grantedScopes?: string;
	};
	status?: string;
};

interface FBSdk {
	init(options: { appId: string; cookie: boolean; xfbml: boolean; version: string }): void;
	login(
		callback: (response: FBLoginResponse) => void,
		options: { scope: string; return_scopes?: boolean },
	): void;
}

declare global {
	interface Window {
		FB?: FBSdk;
	}
}

export {};
