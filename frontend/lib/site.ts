// Company details used by the legal pages, footer and metadata.
// ponytail: legalName, address and governingLaw are placeholders until the real entity exists; swap here only.
export const site = {
	productName: "TumarAgent",
	legalName: "TumarAgent",
	address: "100 Placeholder Street, Example City, Example Country",
	governingLaw: "Example Country",
	domain: "tumaragent.com",
	supportEmail: "support@tumaragent.com",
	privacyEmail: "privacy@tumaragent.com",
	conversationRetentionDays: 90,
	// Landing-page offer copy. shortcut: credits is hand-copied from the admin's signup credits setting and endsOn is not
	// enforced by the backend; wire both to the API/backend if the offer outlives this one-month run.
	signupBonus: { credits: 100, endsOn: "2026-12-12" },
	// Bump when the terms or privacy policy change materially; sign-up stores the version accepted
	termsVersion: "2026-10-29",
	legalUpdated: "October 29, 2026",
};
