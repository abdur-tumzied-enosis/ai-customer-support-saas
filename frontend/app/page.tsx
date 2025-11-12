import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ArrowRight, Bot, FileText, MessageSquare, Zap, Shield, Users } from "lucide-react";
import ThemeToggle from "@/components/theme/ThemeToggle";

export default function LandingPage() {
	return (
		<div className="min-h-screen flex flex-col bg-background">
			{/* Header */}
			<header className="border-b border-border">
				<div className="container mx-auto px-6 py-4 flex items-center justify-between">
					<div className="text-xl font-semibold">Auto Magic Pro</div>
					<div className="flex items-center gap-4">
						<ThemeToggle />
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
									Your data is encrypted and secure. We take privacy seriously
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

			{/* CTA Section */}
			<section className="py-20 px-6">
				<div className="container mx-auto max-w-4xl text-center space-y-6">
					<h2 className="text-3xl font-bold">Ready to Get Started?</h2>
					<p className="text-xl text-muted-foreground">
						Join thousands of businesses using AI to enhance their customer service
					</p>
					<Button size="lg" asChild>
						<Link href="/sign-up">
							Create Your Account
							<ArrowRight className="ml-2 h-4 w-4" />
						</Link>
					</Button>
				</div>
			</section>

			{/* Footer */}
			<footer className="border-t border-border py-8 px-6">
				<div className="container mx-auto max-w-6xl">
					<div className="flex flex-col md:flex-row items-center justify-between gap-4">
						<div className="text-sm text-muted-foreground">
							© 2024 Auto Magic Pro. All rights reserved.
						</div>
						<div className="flex items-center gap-6 text-sm">
							<Link href="/sign-in" className="text-muted-foreground hover:text-foreground">
								Sign in
							</Link>
							<Link href="/sign-up" className="text-muted-foreground hover:text-foreground">
								Sign up
							</Link>
						</div>
					</div>
				</div>
			</footer>
		</div>
	);
}
