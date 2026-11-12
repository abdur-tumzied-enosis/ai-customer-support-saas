import type { Metadata } from "next";
import Link from "next/link";
import { Fraunces, Hind_Siliguri } from "next/font/google";
import { ArrowRight, FileText, Image as ImageIcon, Languages, ListChecks, ShoppingBag, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import ThemeToggle from "@/components/theme/ThemeToggle";
import SiteFooter from "@/components/layout/SiteFooter";
import Logo from "@/components/layout/Logo";
import PricingSection from "@/components/billing/PricingSection";
import { site } from "@/lib/site";
import { cn } from "@/lib/utils";

const display = Fraunces({ subsets: ["latin"], variable: "--font-display" });
const body = Hind_Siliguri({ subsets: ["bengali", "latin"], weight: ["400", "500", "600", "700"], variable: "--font-body" });

export const metadata: Metadata = {
	description:
		"TumarAgent answers your customers on Messenger, WhatsApp and Instagram in Bangla and English, finds products and takes orders. Start with free credits, no card needed.",
};

// Re-check the offer deadline hourly so the banner disappears on its own.
export const revalidate = 3600;

const focus = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background rounded-sm";

const steps = [
	{ icon: Upload, title: "Connect your channel", text: "Messenger, WhatsApp, Instagram or a chat widget on your own site." },
	{ icon: FileText, title: "Teach it your shop", text: "Upload documents, import your product catalog and write instructions in your own words." },
	{ icon: ListChecks, title: "Let it reply", text: "It answers customers, finds products and takes orders from the chat." },
];

const abilities = [
	{ icon: Languages, title: "Any language", text: "Customers write in their own language, Bangla, English or another, and get a reply in the same one.", wide: true },
	{ icon: ShoppingBag, title: "Takes orders from chat", text: "Collects the order details in the conversation, then sets them up from your documents." },
	{ icon: ImageIcon, title: "Product and photo search", text: "Finds the right item from your catalog, including from a photo a customer sends." },
	{ icon: FileText, title: "Your catalog, your rules", text: "Import products, upload documents and add custom instructions. The agent answers from what you gave it.", wide: true },
];

const faqs = [
	{ q: "What do I get for free?", a: `${site.signupBonus.credits} credits when you create an account and verify your email. One AI reply uses 1 credit, so that is about ${site.signupBonus.credits} replies. No card is needed.` },
	{ q: "What happens when the free credits run out?", a: "Buy a credit pack. Credits never expire, and bigger packs cost less per reply. Online payment is coming soon; until then we add credits to your project when you contact us." },
	{ q: "Which channels does it work on?", a: "Messenger, WhatsApp, Instagram and a web chat widget, all from one dashboard." },
	{ q: "Is my customers' data safe?", a: `Your data is never sold or used to train AI models. Conversations are kept for ${site.conversationRetentionDays} days, and you can ask us to delete your data any time.` },
];

function Chat({ from, children }: { from: "customer" | "agent"; children: React.ReactNode }) {
	const agent = from === "agent";
	return (
		<div className={cn("flex", agent ? "justify-end" : "justify-start")}>
			<p className={cn("max-w-[85%] rounded-2xl px-4 py-2.5 text-[15px] leading-relaxed", agent ? "bg-primary text-primary-foreground rounded-br-sm" : "bg-muted rounded-bl-sm")}>
				{children}
			</p>
		</div>
	);
}

export default function LandingPage() {
	const { credits, endsOn } = site.signupBonus;
	const live = new Date() <= new Date(`${endsOn}T23:59:59+06:00`);
	const endDate = new Date(`${endsOn}T12:00:00+06:00`).toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric", timeZone: "Asia/Dhaka" });

	return (
		<div className={cn("landing min-h-screen flex flex-col bg-background text-foreground font-[family-name:var(--font-body)]", display.variable, body.variable)}>
			{live && (
				<p className="bg-[hsl(var(--marigold))] text-[hsl(28_20%_9%)] text-center text-sm font-medium px-6 py-2">
					Launch offer: {credits} free credits for every new account until {endDate}. No card needed.
				</p>
			)}

			<header className="border-b border-border px-6">
				<div className="container mx-auto max-w-6xl py-4 flex items-center justify-between gap-4">
					<Link href="/" aria-label="TumarAgent home" className={cn("text-foreground hover:no-underline", focus)}>
						<Logo className="h-7" />
					</Link>
					<nav aria-label="Main" className="flex items-center gap-1 sm:gap-3">
						<a href="#how" className={cn("hidden md:inline px-2 text-sm text-muted-foreground hover:text-foreground hover:no-underline", focus)}>How it works</a>
						<a href="#pricing" className={cn("hidden sm:inline px-2 text-sm text-muted-foreground hover:text-foreground hover:no-underline", focus)}>Pricing</a>
						<ThemeToggle />
						<Button variant="ghost" asChild className="hidden sm:inline-flex">
							<Link href="/sign-in">Sign in</Link>
						</Button>
						<Button asChild>
							<Link href="/sign-up">{live ? "Start free" : "Get started"}</Link>
						</Button>
					</nav>
				</div>
			</header>

			<main>
				{/* Hero */}
				<section className="py-14 md:py-24 px-6">
					<div className="container mx-auto max-w-6xl grid gap-12 lg:grid-cols-[1.1fr_0.9fr] items-center">
						<div className="space-y-6">
							<h1 className="text-4xl sm:text-5xl lg:text-6xl font-semibold tracking-tight leading-[1.08]">
								Never leave a customer on &ldquo;seen&rdquo; again.
							</h1>
							<p className="text-lg md:text-xl text-muted-foreground max-w-xl">
								TumarAgent answers your Messenger, WhatsApp and Instagram chats in Bangla and English, finds products and takes orders, using your own catalog and instructions.
								{live ? ` Start with ${credits} free credits, no card needed.` : ""}
							</p>
							<div className="flex flex-wrap items-center gap-3">
								<Button size="lg" asChild>
									<Link href="/sign-up">
										{live ? `Claim ${credits} free credits` : "Get started"}
										<ArrowRight className="ml-2 h-4 w-4" aria-hidden />
									</Link>
								</Button>
								<Button size="lg" variant="outline" asChild>
									<a href="#pricing">See pricing</a>
								</Button>
							</div>
							{live && <p className="text-sm text-muted-foreground">Free credits until {endDate}. About {credits} AI replies.</p>}
						</div>

						<figure className="rounded-2xl border border-border bg-card p-5 sm:p-6 space-y-3 shadow-sm" aria-label="Example conversation">
							<figcaption className="text-xs uppercase tracking-wider text-muted-foreground pb-1">Example conversation</figcaption>
							<Chat from="customer">ভাই, এই পাঞ্জাবিটা কি L সাইজে আছে?</Chat>
							<Chat from="agent">জি, L সাইজে আছে। কোন রঙটা নেবেন? আমি ছবি পাঠিয়ে দিচ্ছি।</Chat>
							<Chat from="customer">Do you deliver outside Dhaka?</Chat>
							<Chat from="agent">Yes. Send me your name, phone number and address and I will set up your order.</Chat>
						</figure>
					</div>
				</section>

				{/* How it works */}
				<section id="how" className="py-14 md:py-20 px-6 bg-muted/60 scroll-mt-4">
					<div className="container mx-auto max-w-6xl">
						<h2 className="text-3xl md:text-4xl font-semibold tracking-tight max-w-2xl">Set up in three steps</h2>
						<ol className="mt-10 grid gap-8 md:grid-cols-3">
							{steps.map(({ icon: Icon, title, text }, i) => (
								<li key={title} className="space-y-3">
									<div className="flex items-center gap-3">
										<span className="flex h-9 w-9 items-center justify-center rounded-full bg-primary text-primary-foreground text-sm font-semibold" aria-hidden>
											{i + 1}
										</span>
										<Icon className="h-5 w-5 text-muted-foreground" aria-hidden />
									</div>
									<h3 className="text-lg font-semibold">{title}</h3>
									<p className="text-muted-foreground">{text}</p>
								</li>
							))}
						</ol>
					</div>
				</section>

				{/* What it does */}
				<section className="py-14 md:py-20 px-6">
					<div className="container mx-auto max-w-6xl">
						<h2 className="text-3xl md:text-4xl font-semibold tracking-tight max-w-2xl">Made for shops that sell in chat</h2>
						<div className="mt-10 grid gap-4 md:grid-cols-2">
							{abilities.map(({ icon: Icon, title, text, wide }) => (
								<div key={title} className={cn("rounded-xl border border-border bg-card p-6", wide && "md:min-h-44")}>
									<Icon className="h-6 w-6 text-primary" aria-hidden />
									<h3 className="mt-4 text-lg font-semibold">{title}</h3>
									<p className="mt-1 text-muted-foreground">{text}</p>
								</div>
							))}
						</div>
					</div>
				</section>

				<PricingSection />

				{/* Trust + FAQ */}
				<section className="py-14 md:py-20 px-6 bg-muted/60">
					<div className="container mx-auto max-w-3xl">
						<h2 className="text-3xl md:text-4xl font-semibold tracking-tight">Questions</h2>
						<div className="mt-8 divide-y divide-border border-y border-border">
							{faqs.map(({ q, a }) => (
								<details key={q} className="group py-4">
									<summary className={cn("flex cursor-pointer list-none items-center justify-between gap-4 font-semibold [&::-webkit-details-marker]:hidden", focus)}>
										{q}
										<span aria-hidden className="text-xl text-muted-foreground transition-transform group-open:rotate-45">+</span>
									</summary>
									<p className="mt-2 text-muted-foreground">{a}</p>
								</details>
							))}
						</div>
					</div>
				</section>

				{/* Final CTA */}
				<section className="py-16 md:py-24 px-6">
					<div className="container mx-auto max-w-3xl text-center space-y-6">
						<h2 className="text-3xl md:text-4xl font-semibold tracking-tight">Let the next customer message get an instant answer</h2>
						<Button size="lg" asChild>
							<Link href="/sign-up">
								{live ? `Claim ${credits} free credits` : "Create your account"}
								<ArrowRight className="ml-2 h-4 w-4" aria-hidden />
							</Link>
						</Button>
						{live && <p className="text-sm text-muted-foreground">No card needed. Offer ends {endDate}.</p>}
					</div>
				</section>
			</main>

			<SiteFooter />
		</div>
	);
}
