import { Suspense, type ReactNode } from "react";
import OrdersShell from "@/components/orders/OrdersShell";

export default function OrderSettingsLayout({ children }: { children: ReactNode }) {
	return (
		<Suspense fallback={null}>
			<OrdersShell>{children}</OrdersShell>
		</Suspense>
	);
}
