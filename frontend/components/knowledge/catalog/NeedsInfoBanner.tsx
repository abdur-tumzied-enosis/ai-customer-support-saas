import { Alert, AlertDescription } from "@/components/ui/alert";

export default function NeedsInfoBanner({ count }: { count: number }) {
	if (count === 0) return null;
	return (
		<Alert>
			<AlertDescription>{count} {count === 1 ? "product needs" : "products need"} info before they can be sold</AlertDescription>
		</Alert>
	);
}
