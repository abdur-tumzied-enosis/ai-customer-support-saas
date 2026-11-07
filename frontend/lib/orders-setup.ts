// Which setup step to show. Derived from the saved settings (D2), never from the URL alone (D3):
// ?step=1 may always go back to Settings; without it, a confirmed setup opens on Products.
export function setupStep(confirmed: boolean, requested: string | null): 1 | 2 {
	if (requested === "1") return 1;
	return confirmed ? 2 : 1;
}
