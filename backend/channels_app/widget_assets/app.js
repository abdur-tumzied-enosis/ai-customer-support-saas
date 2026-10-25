/* Chat widget app: runs inside the iframe on the backend origin (channels_app/widget.py, templates/widget/embed.html).
   ponytail: vanilla DOM, no framework or build step; move to Preact if the UI outgrows render(). */
(function () {
	"use strict";
	const config = JSON.parse(document.getElementById("widget-config").textContent);
	const storageKey = "amw:" + config.key;
	// Who embeds us (CSP frame-ancestors already limited it to an allowed origin). Firefox lacks ancestorOrigins;
	// the referrer can be stripped by the host's policy, then state messages go to "*" (they carry nothing private).
	const parentOrigin = (location.ancestorOrigins && location.ancestorOrigins[0]) || originOf(document.referrer);
	const ICON = '<svg viewBox="0 0 24 24" width="28" height="28" aria-hidden="true"><path fill="currentColor" d="M4 4h16a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H8l-4 4V6a2 2 0 0 1 2-2z"/></svg>';

	let token = load();
	let open = false;
	let pending = false; // the bot is answering
	let pollTimer = null;
	const messages = []; // from the server, oldest first
	const seen = new Set();
	let outbox = []; // {client_id, text, failed} not yet confirmed by the server

	document.documentElement.style.setProperty("--primary", config.primary_color);
	document.documentElement.classList.toggle("left", config.position === "left");

	const launcher = el("button", { class: "launcher", type: "button", "aria-label": "Open chat" });
	launcher.innerHTML = ICON; // constant markup
	const closeButton = el("button", { class: "close", type: "button", "aria-label": "Close chat", text: "×" });
	const log = el("div", { class: "log", role: "log", "aria-live": "polite" });
	const error = el("div", { class: "error", role: "alert", hidden: "" });
	const input = el("textarea", { rows: "1", maxlength: "2000", placeholder: "Type a message…", "aria-label": "Message" });
	const sendButton = el("button", { class: "send", type: "submit", text: "Send" });
	const form = el("form", { class: "composer" }, [input, sendButton]);
	const panel = el("section", { class: "panel", role: "dialog", "aria-label": config.title, hidden: "" }, [
		el("header", {}, [el("h1", { text: config.title }), closeButton]),
		log,
		error,
		form,
	]);
	document.body.append(launcher, panel);

	launcher.addEventListener("click", () => setOpen(true));
	closeButton.addEventListener("click", () => setOpen(false));
	document.addEventListener("keydown", (e) => { if (e.key === "Escape" && open) setOpen(false); });
	input.addEventListener("keydown", (e) => {
		if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); }
	});
	form.addEventListener("submit", (e) => {
		e.preventDefault();
		const text = input.value.trim();
		if (!text) return;
		input.value = "";
		const item = { client_id: uid(), text: text, failed: false };
		outbox.push(item);
		deliver(item);
	});
	window.addEventListener("message", (e) => {
		if (e.source !== window.parent || (parentOrigin && e.origin !== parentOrigin)) return;
		const data = e.data;
		if (!data || data.v !== 1) return;
		if (data.type === "open") setOpen(true);
		if (data.type === "close") setOpen(false);
	});

	function setOpen(value) {
		open = value;
		tellParent(); // resize the iframe first
		panel.hidden = !open;
		launcher.hidden = open;
		clearTimeout(pollTimer);
		if (open) {
			render();
			input.focus();
			tick();
		} else {
			launcher.focus();
		}
	}

	function tellParent() {
		window.parent.postMessage({ v: 1, type: "state", state: open ? "open" : "closed", position: config.position }, parentOrigin || "*");
	}

	// --- Server -------------------------------------------------------------------------------------------------

	async function api(path, options, retried) {
		if (!token) await newSession();
		const res = await fetch(path, {
			method: (options && options.method) || "GET",
			headers: { Authorization: "Bearer " + token, "Content-Type": "application/json" },
			body: options && options.body ? JSON.stringify(options.body) : undefined,
		});
		if (res.status === 401 && !retried) { // expired, or the widget's key was rotated: start a fresh session
			token = null;
			save(null);
			return api(path, options, true);
		}
		if (!res.ok) throw new Error("HTTP " + res.status);
		return (await res.json()).data;
	}

	async function newSession() {
		const res = await fetch("/api/widget/" + encodeURIComponent(config.key) + "/session", { method: "POST" });
		if (!res.ok) throw new Error("HTTP " + res.status);
		token = (await res.json()).data.token;
		save(token);
	}

	async function refresh() {
		if (!token) return; // no session until the visitor sends something
		const last = messages[messages.length - 1];
		// A few seconds of overlap catches rows committed slightly out of created_at order; ids dedupe them
		const after = last ? "?after=" + encodeURIComponent(new Date(Date.parse(last.created_at) - 5000).toISOString()) : "";
		const data = await api("/api/widget/messages" + after);
		data.messages.forEach(add);
		pending = data.pending;
	}

	function add(message) {
		if (seen.has(message.id)) return;
		seen.add(message.id);
		messages.push(message);
		messages.sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at));
		if (message.client_id) outbox = outbox.filter((o) => o.client_id !== message.client_id);
	}

	async function deliver(item) {
		item.failed = false;
		render();
		try {
			const data = await api("/api/widget/messages", { method: "POST", body: { text: item.text, client_id: item.client_id } });
			add(data.message);
			pending = true;
		} catch (e) {
			item.failed = true;
		}
		render();
		schedule();
	}

	async function tick() {
		try {
			await refresh();
			error.hidden = true;
		} catch (e) {
			error.textContent = "Connection problem. Retrying…";
			error.hidden = false;
		}
		render();
		schedule();
	}

	function schedule() {
		clearTimeout(pollTimer);
		if (open && token) pollTimer = setTimeout(tick, pending ? 1000 : 10000);
	}

	// --- Rendering ----------------------------------------------------------------------------------------------

	function render() {
		const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
		log.replaceChildren(bubble("bot", config.welcome_message));
		messages.forEach((m) => log.append(bubble(m.sender === "customer" ? "me" : m.sender, m.text)));
		outbox.forEach((item) => {
			const b = bubble("me sending", item.text);
			if (item.failed) {
				const retry = el("button", { class: "retry", type: "button", text: "Not sent. Tap to retry" });
				retry.addEventListener("click", () => deliver(item));
				b.append(retry);
			}
			log.append(b);
		});
		if (pending) log.append(el("div", { class: "bubble bot typing", "aria-label": "Typing", text: "• • •" }));
		if (atBottom) log.scrollTop = log.scrollHeight;
	}

	function bubble(kind, text) {
		const b = el("div", { class: "bubble " + kind });
		b.append(format(text || ""));
		return b;
	}

	// Text only: **bold** and http(s) links become elements, everything else stays text (never innerHTML)
	function format(text) {
		const fragment = document.createDocumentFragment();
		const pattern = /\*\*([^*\n]+)\*\*|(https?:\/\/[^\s<>()"]+[^\s<>()".,;:!?'])/g;
		let index = 0;
		let match;
		while ((match = pattern.exec(text))) {
			fragment.append(text.slice(index, match.index));
			if (match[1]) {
				fragment.append(el("strong", { text: match[1] }));
			} else {
				fragment.append(el("a", { href: match[2], target: "_blank", rel: "noopener noreferrer", text: match[2] }));
			}
			index = pattern.lastIndex;
		}
		fragment.append(text.slice(index));
		return fragment;
	}

	function el(tag, attrs, children) {
		const node = document.createElement(tag);
		for (const name in attrs) {
			if (name === "text") node.textContent = attrs[name];
			else node.setAttribute(name, attrs[name]);
		}
		(children || []).forEach((child) => node.append(child));
		return node;
	}

	// --- Helpers ------------------------------------------------------------------------------------------------

	function load() {
		try { return localStorage.getItem(storageKey); } catch (e) { return null; } // blocked or partitioned storage
	}

	function save(value) {
		try { value ? localStorage.setItem(storageKey, value) : localStorage.removeItem(storageKey); } catch (e) { /* session lasts this page */ }
	}

	function uid() {
		return crypto.randomUUID ? crypto.randomUUID() : Date.now().toString(36) + Math.random().toString(36).slice(2);
	}

	function originOf(url) {
		try { return url ? new URL(url).origin : ""; } catch (e) { return ""; }
	}

	tellParent();
})();
