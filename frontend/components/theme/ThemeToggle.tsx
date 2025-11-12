"use client";

import { useTheme } from "next-themes";
import { useEffect, useState } from "react";
import { Sun, Moon } from "lucide-react";
import { Button } from "@/components/ui/button";

export default function ThemeToggle() {
	const { theme, setTheme, systemTheme } = useTheme();
	const [mounted, setMounted] = useState(false);

	// Prevent hydration mismatch by only rendering after mount
	useEffect(() => {
		setMounted(true);
	}, []);

	const effective = theme === "system" ? systemTheme : theme;
	const isDark = effective === "dark";

	// Render a placeholder during SSR to match initial client render
	if (!mounted) {
		return (
			<Button
				variant="outline"
				size="icon"
				aria-label="Toggle theme"
				title="Toggle theme"
			>
				<Moon className="h-4 w-4" />
			</Button>
		);
	}

	return (
		<Button
			variant="outline"
			size="icon"
			onClick={() => setTheme(isDark ? "light" : "dark")}
			aria-label={isDark ? "Switch to light mode" : "Switch to dark mode"}
			title={isDark ? "Switch to light mode" : "Switch to dark mode"}
		>
			{isDark ? (
				<Sun className="h-4 w-4" />
			) : (
				<Moon className="h-4 w-4" />
			)}
		</Button>
	);
}


