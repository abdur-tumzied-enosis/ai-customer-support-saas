"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "@/lib/api";
import { orderApi, type SetupState } from "@/lib/orders-api";
import { formatError } from "@/lib/errors";

const POLL_MS = 2000;

// The screen renders only what the server says. Polls while a run is active; never starts a second run.
export function useSetupState(projectId: string, kbId: string) {
	const [state, setState] = useState<SetupState | null>(null);
	const [error, setError] = useState<string | null>(null);
	const timer = useRef<ReturnType<typeof setTimeout>>();

	const refresh = useCallback(async () => {
		const res = await orderApi.setupState(projectId);
		setState(res.data);
		return res.data;
	}, [projectId]);

	const poll = useCallback(async function poll(): Promise<void> {
		clearTimeout(timer.current);
		try {
			const next = await refresh();
			if (next.status === "running") timer.current = setTimeout(poll, POLL_MS);
		} catch (e) {
			setError(formatError(e));
		}
	}, [refresh]);

	// Starts a run (resume or restart). A run that is already going is not an error: just keep polling it.
	const run = useCallback(
		async (mode: "resume" | "restart") => {
			setError(null);
			try {
				await orderApi.runSetup(projectId, kbId, mode);
			} catch (e) {
				if (!(e instanceof ApiError && e.code === "setup_running")) {
					setError(formatError(e));
					return;
				}
			}
			await poll();
		},
		[projectId, kbId, poll],
	);

	useEffect(() => {
		let cancelled = false;
		(async () => {
			try {
				const first = await refresh();
				if (cancelled) return;
				if (first.status === "not_started") await run("resume");
				else await poll();
			} catch (e) {
				if (!cancelled) setError(formatError(e));
			}
		})();
		return () => {
			cancelled = true;
			clearTimeout(timer.current);
		};
	}, [refresh, run, poll]);

	return { state, error, run };
}
