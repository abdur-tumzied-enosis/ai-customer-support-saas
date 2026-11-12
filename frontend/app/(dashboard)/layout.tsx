import { AppProviders } from "@/app/providers";
import DashboardShell from "@/components/layout/DashboardShell";

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
	return (
		<AppProviders>
			<DashboardShell>{children}</DashboardShell>
		</AppProviders>
	);
}
