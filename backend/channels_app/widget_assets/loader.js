/* Auto Magic chat widget loader. The only code that runs on the host page: it adds the chat iframe and resizes it.
   Usage: <script src="https://<backend>/widget/loader.js" data-widget-key="wgt_..." async></script>
   Optional: AutoMagic("open") / AutoMagic("close"); calls made before this loads are queued in AutoMagic.q. */
(function () {
	"use strict";
	if (window.__autoMagicWidget) return; // pasted twice, or re-run by an SPA router
	var script = document.currentScript;
	var key = script && script.getAttribute("data-widget-key");
	if (!key) {
		console.warn("Auto Magic widget: data-widget-key is missing on the script tag");
		return;
	}
	window.__autoMagicWidget = true;
	var origin = new URL(script.src).origin;
	var iframe = document.createElement("iframe");
	var state = null; // set by the iframe's first message; hidden until then (and forever if the embed is refused)
	var position = "right";
	var mobile = window.matchMedia("(max-width: 480px)");

	iframe.src = origin + "/embed/" + encodeURIComponent(key) + "/";
	iframe.title = "Chat";
	// No allow-top-navigation: the widget can never navigate the host page
	iframe.setAttribute("sandbox", "allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox");

	function css(props) {
		// !important inline beats host stylesheets like `* { all: unset !important }`
		for (var name in props) iframe.style.setProperty(name, props[name], "important");
	}

	function layout() {
		var open = state === "open";
		var hidden = state === null;
		var full = open && mobile.matches;
		css({
			position: "fixed", border: "0", margin: "0", padding: "0", "z-index": "2147483647",
			display: "block", visibility: hidden ? "hidden" : "visible", background: "transparent", "color-scheme": "light", "max-width": "100vw", "max-height": "100dvh",
			bottom: full ? "0" : "16px",
			left: position === "left" ? (full ? "0" : "16px") : "auto",
			right: position === "right" ? (full ? "0" : "16px") : "auto",
			width: full ? "100vw" : open ? "380px" : "64px",
			height: full ? "100dvh" : open ? "min(640px, calc(100dvh - 32px))" : "64px",
		});
	}

	function send(type) {
		iframe.contentWindow && iframe.contentWindow.postMessage({ v: 1, type: type }, origin);
	}

	window.addEventListener("message", function (event) {
		if (event.origin !== origin || event.source !== iframe.contentWindow) return;
		var data = event.data;
		if (!data || data.v !== 1 || data.type !== "state") return;
		state = data.state === "open" ? "open" : "closed";
		position = data.position === "left" ? "left" : "right";
		layout();
	});
	mobile.addEventListener && mobile.addEventListener("change", layout);

	var queued = (window.AutoMagic && window.AutoMagic.q) || [];
	window.AutoMagic = function (command) {
		if (command === "open" || command === "close") send(command);
	};
	iframe.addEventListener("load", function () {
		queued.forEach(function (args) { window.AutoMagic.apply(null, args); });
		queued = [];
	});

	layout();
	(document.body || document.documentElement).appendChild(iframe);
})();
