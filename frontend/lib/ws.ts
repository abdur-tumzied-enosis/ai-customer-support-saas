type ReconnectOptions = {
	maxRetries?: number;
	initialDelayMs?: number;
};

export function createReconnectingWebSocket(url: string, { maxRetries = 5, initialDelayMs = 500 }: ReconnectOptions = {}) {
	let ws: WebSocket | null = null;
	let retries = 0;
	let delay = initialDelayMs;

	const listeners: {
		open: Array<(ev: Event) => void>;
		message: Array<(ev: MessageEvent) => void>;
		error: Array<(ev: Event) => void>;
		close: Array<(ev: CloseEvent) => void>;
	} = { open: [], message: [], error: [], close: [] };

	function notify<K extends keyof typeof listeners>(type: K, ev: Parameters<(typeof listeners)[K][number]>[0]) {
		for (const cb of listeners[type]) cb(ev as any);
	}

	function connect() {
		ws = new WebSocket(url);
		ws.onopen = (ev) => {
			retries = 0;
			delay = initialDelayMs;
			notify("open", ev);
		};
		ws.onmessage = (ev) => notify("message", ev);
		ws.onerror = (ev) => notify("error", ev);
		ws.onclose = (ev) => {
			notify("close", ev);
			if (retries < maxRetries) {
				retries++;
				setTimeout(connect, delay);
				delay = Math.min(delay * 2, 8000);
			}
		};
	}

	connect();

	return {
		send: (data: string | ArrayBufferLike | Blob | ArrayBufferView) => ws?.send(data),
		close: () => ws?.close(),
		onopen: (cb: (ev: Event) => void) => listeners.open.push(cb),
		onmessage: (cb: (ev: MessageEvent) => void) => listeners.message.push(cb),
		onerror: (cb: (ev: Event) => void) => listeners.error.push(cb),
		onclose: (cb: (ev: CloseEvent) => void) => listeners.close.push(cb),
	};
}


