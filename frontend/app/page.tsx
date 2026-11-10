import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ArrowRight, Bot, FileText, MessageSquare, Zap, Shield, Users } from "lucide-react";
import ThemeToggle from "@/components/theme/ThemeToggle";
import SiteFooter from "@/components/layout/SiteFooter";
import Logo from "@/components/layout/Logo";
import PricingSection from "@/components/billing/PricingSection";

export default function LandingPage() {
	return (
		<div className="min-h-screen flex flex-col bg-background">
			{/* Header */}
			<header className="border-b border-border">
				<div className="container mx-auto px-6 py-4 flex items-center justify-between">
					<Logo className="h-7" />
					<div className="flex items-center gap-4">
						<ThemeToggle />
						<Button variant="ghost" asChild className="hidden sm:inline-flex">
							<a href="#pricing">Pricing</a>
						</Button>
						<Button variant="ghost" asChild>
							<Link href="/sign-in">Sign in</Link>
						</Button>
						<Button asChild>
							<Link href="/sign-up">Get Started</Link>
						</Button>
					</div>
				</div>
			</header>

			{/* Hero Section */}
			<section className="flex-1 flex items-center justify-center py-20 px-6">
				<div className="container mx-auto max-w-4xl text-center space-y-8">
					<h1 className="text-5xl md:text-6xl font-bold tracking-tight">
						AI-Powered Customer
						<br />
						<span className="text-primary">Assistance Platform</span>
					</h1>
					<p className="text-xl text-muted-foreground max-w-2xl mx-auto">
						Automate customer interactions across multiple channels with intelligent AI agents
						that learn from your knowledge base and follow your custom instructions.
					</p>
					<div className="flex items-center justify-center gap-4 flex-wrap">
						<Button size="lg" asChild>
							<Link href="/sign-up">
								Get Started
								<ArrowRight className="ml-2 h-4 w-4" />
							</Link>
						</Button>
						<Button size="lg" variant="outline" asChild>
							<Link href="/sign-in">Sign in</Link>
						</Button>
					</div>
				</div>
			</section>

			{/* Features Section */}
			<section className="py-20 px-6 bg-muted/50">
				<div className="container mx-auto max-w-6xl">
					<h2 className="text-3xl font-bold text-center mb-12">Powerful Features</h2>
					<div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
						<Card>
							<CardHeader>
								<Bot className="h-8 w-8 text-primary mb-2" />
								<CardTitle>AI Agents</CardTitle>
								<CardDescription>
									Intelligent AI agents that understand context and provide accurate responses
								</CardDescription>
							</CardHeader>
						</Card>
						<Card>
							<CardHeader>
								<MessageSquare className="h-8 w-8 text-primary mb-2" />
								<CardTitle>Multi-Channel</CardTitle>
								<CardDescription>
									Connect with customers across Facebook, WhatsApp, and other platforms
								</CardDescription>
							</CardHeader>
						</Card>
						<Card>
							<CardHeader>
								<FileText className="h-8 w-8 text-primary mb-2" />
								<CardTitle>Knowledge Base</CardTitle>
								<CardDescription>
									Upload documents and create custom instructions to guide your agent
								</CardDescription>
							</CardHeader>
						</Card>
						<Card>
							<CardHeader>
								<Zap className="h-8 w-8 text-primary mb-2" />
								<CardTitle>Fast Responses</CardTitle>
								<CardDescription>
									Get instant, accurate responses to customer inquiries 24/7
								</CardDescription>
							</CardHeader>
						</Card>
						<Card>
							<CardHeader>
								<Shield className="h-8 w-8 text-primary mb-2" />
								<CardTitle>Secure & Private</CardTitle>
								<CardDescription>
									Your data is never sold or used to train AI models, and you can delete it any time
								</CardDescription>
							</CardHeader>
						</Card>
						<Card>
							<CardHeader>
								<Users className="h-8 w-8 text-primary mb-2" />
								<CardTitle>Team Collaboration</CardTitle>
								<CardDescription>
									Share knowledge and collaborate with your team seamlessly
								</CardDescription>
							</CardHeader>
						</Card>
					</div>
				</div>
			</section>

			<PricingSection />

			{/* CTA Section */}
			<section className="py-20 px-6">
				<div className="container mx-auto max-w-4xl text-center space-y-6">
					<h2 className="text-3xl font-bold">Ready to Get Started?</h2>
					<p className="text-xl text-muted-foreground">
						Start with free credits. Set up your assistant in minutes.
					</p>
					<Button size="lg" asChild>
						<Link href="/sign-up">
							Create Your Account
							<ArrowRight className="ml-2 h-4 w-4" />
						</Link>
					</Button>
				</div>
			</section>

			<SiteFooter />
		</div>
	);
}
