import { ReactNode } from "react";

interface StatsGridProps {
	children: ReactNode;
	columns?: 2 | 3 | 4;
}

export function StatsGrid({ children, columns = 4 }: StatsGridProps) {
	const gridClass = {
		2: "grid-cols-1 md:grid-cols-2",
		3: "grid-cols-1 md:grid-cols-2 lg:grid-cols-3",
		4: "grid-cols-1 md:grid-cols-2 lg:grid-cols-4",
	}[columns];

	return (
		<div className={`grid ${gridClass} gap-4`}>
			{children}
		</div>
	);
}

