"use client";

import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";
import { AlertCircle, CheckCircle2, Info, X } from "lucide-react";
import { cn } from "@/lib/utils";

type Kind = "success" | "error" | "info";
type Toast = { id: number; kind: Kind; message: string };

type ToastApi = Record<Kind, (message: string) => void>;

const ToastContext = createContext<ToastApi | null>(null);

// Errors stay longer: they usually need the user to read and act
const DURATION_MS: Record<Kind, number> = { success: 4000, info: 4000, error: 7000 };
const MAX_VISIBLE = 3;

const ICON: Record<Kind, typeof Info> = { success: CheckCircle2, error: AlertCircle, info: Info };

export function ToastProvider({ children }: { children: ReactNode }) {
	const [toasts, setToasts] = useState<Toast[]>([]);
	const nextId = useRef(0);

	const dismiss = useCallback((id: number) => setToasts((list) => list.filter((t) => t.id !== id)), []);

	const show = useCallback(
		(kind: Kind, message: string) => {
			const id = ++nextId.current;
			setToasts((list) => [...list.slice(-(MAX_VISIBLE - 1)), { id, kind, message }]);
			window.setTimeout(() => dismiss(id), DURATION_MS[kind]);
		},
		[dismiss]
	);

	const api = useMemo<ToastApi>(
		() => ({
			success: (m) => show("success", m),
			error: (m) => show("error", m),
			info: (m) => show("info", m),
		}),
		[show]
	);

	return (
		<ToastContext.Provider value={api}>
			{children}
			<div className="pointer-events-none fixed inset-x-0 bottom-4 z-[100] flex flex-col items-center gap-2 px-4">
				{toasts.map((t) => {
					const Icon = ICON[t.kind];
					return (
						<div
							key={t.id}
							role={t.kind === "error" ? "alert" : "status"}
							className={cn(
								"pointer-events-auto flex w-full max-w-md items-start gap-3 rounded-lg px-4 py-3 text-sm shadow-lg animate-in fade-in slide-in-from-bottom-2",
								t.kind === "error" ? "bg-destructive text-destructive-foreground" : "bg-foreground text-background"
							)}
						>
							<Icon className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
							<p className="flex-1 leading-snug">{t.message}</p>
							<button
								type="button"
								onClick={() => dismiss(t.id)}
								aria-label="Dismiss"
								className="shrink-0 rounded opacity-70 hover:opacity-100 focus-visible:outline-none focus-visible:ring-2"
							>
								<X className="h-4 w-4" />
							</button>
						</div>
					);
				})}
			</div>
		</ToastContext.Provider>
	);
}

export function useToast(): ToastApi {
	const ctx = useContext(ToastContext);
	if (!ctx) throw new Error("useToast must be used inside ToastProvider");
	return ctx;
}
