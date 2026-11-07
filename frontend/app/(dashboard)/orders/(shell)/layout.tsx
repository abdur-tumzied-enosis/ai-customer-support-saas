import type { ReactNode } from "react";
import OrdersShell from "@/components/orders/OrdersShell";

export default function OrdersLayout({ children }: { children: ReactNode }) {
	return <OrdersShell>{children}</OrdersShell>;
}
