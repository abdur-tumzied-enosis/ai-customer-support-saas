"use client";

import { Suspense, useEffect, useState, type ReactNode } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Check } from "lucide-react";
import { Button } from "@/components/ui/button";
import { orderApi } from "@/lib/orders-api";
import { setupStep } from "@/lib/orders-setup";
import { SetupDirtyContext } from "@/components/orders/setup/SetupDirty";
import { useAuth } from "@/hooks/useAuth";
import { cn } from "@/lib/utils";

const STEPS = ["Settings", "Products"];

function SetupHeader({ dirty }: { dirty: boolean }) {
	const { projects } = useAuth();
	const project = projects.length > 0 ? projects[0] : null;
	const requested = useSearchParams().get("step");
	const [confirmed, setConfirmed] = useState<boolean | null>(null);

	useEffect(() => {
		if (!project) return;
		orderApi.settings(String(project.id)).then((res) => setConfirmed(res.data.settings_confirmed_at !== null)).catch(() => setConfirmed(false));
	}, [project, requested]);

	const current = setupStep(confirmed ?? false, requested);

	return (
		<header className="flex flex-wrap items-center justify-between gap-4 border-b pb-4">
			<div className="flex flex-wrap items-center gap-6">
				<h1 className="text-2xl font-semibold">Set up orders</h1>
				<ol className="flex items-center gap-4 text-sm" aria-label="Setup steps">
					{STEPS.map((label, i) => {
						const n = i + 1;
						const active = current === n;
						const done = current > n;
						return (
							<li key={label} aria-current={active ? "step" : undefined} className={cn("flex items-center gap-2", active ? "font-medium" : "text-muted-foreground")}>
								<span className={cn("flex h-5 w-5 items-center justify-center rounded-full border text-xs", active && "border-primary bg-primary text-primary-foreground")}>
									{done ? <Check className="h-3 w-3" aria-hidden /> : n}
								</span>
								{label}
							</li>
						);
					})}
				</ol>
			</div>
			<Button
				variant="outline"
				size="sm"
				asChild
			>
				<Link
					href="/orders"
					onClick={(e) => {
						if (dirty && !window.confirm("Close without saving?")) e.preventDefault();
					}}
				>
					Close
				</Link>
			</Button>
		</header>
	);
}

export default function OrderSetupLayout({ children }: { children: ReactNode }) {
	const [dirty, setDirty] = useState(false);
	return (
		<SetupDirtyContext.Provider value={setDirty}>
			<div className="mx-auto max-w-3xl space-y-6">
				<Suspense fallback={null}>
					<SetupHeader dirty={dirty} />
				</Suspense>
				{children}
			</div>
		</SetupDirtyContext.Provider>
	);
}
