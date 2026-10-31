import type { ReactNode } from "react";
import { ChevronDown } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

type Props = {
	title: ReactNode;
	description?: ReactNode;
	contentClassName?: string;
	children: ReactNode;
};

// ponytail: native <details> does the open/close, no state; starts open, not remembered across reloads.
export default function CollapsibleCard({ title, description, contentClassName, children }: Props) {
	return (
		<Card>
			<details open className="group">
				<summary className="cursor-pointer list-none [&::-webkit-details-marker]:hidden">
					<CardHeader>
						<CardTitle className="flex items-center gap-2">
							{title}
							<ChevronDown className="ml-auto h-5 w-5 text-muted-foreground transition-transform group-open:rotate-180" />
						</CardTitle>
						{description && <CardDescription>{description}</CardDescription>}
					</CardHeader>
				</summary>
				<CardContent className={contentClassName}>{children}</CardContent>
			</details>
		</Card>
	);
}
