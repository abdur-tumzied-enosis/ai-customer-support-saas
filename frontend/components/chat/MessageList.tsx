import { cn } from "@/lib/utils";
import { API_BASE_URL } from "@/lib/api";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { User, Bot, Headphones } from "lucide-react";
import { formatCredits } from "@/lib/billing-api";

export type ChatMessage = {
	id: string;
	role: "user" | "assistant" | "system";
	content: string;
	sender?: "customer" | "bot" | "staff";
	hasPhoto?: boolean;
	photoUrls?: string[];
	photoMatches?: string;
	credits?: { label: string; credits: string }[];
};

type MessageListProps = {
	messages: ChatMessage[];
};

const withApiBase = (path: string) => `${API_BASE_URL}${path}`;

export default function MessageList({ messages }: MessageListProps) {
	return (
		<div className="space-y-4 pb-2">
			{messages.map((m, idx) => {
				const isUser = m.role === "user";
				const isStaff = m.sender === "staff";
				
				return (
					<div
						key={m.id}
						className={cn(
							"flex items-start gap-3 animate-in fade-in slide-in-from-bottom-2 duration-300",
							isUser ? "flex-row-reverse" : "flex-row"
						)}
					>
						<Avatar className={cn(
							"h-8 w-8 shrink-0",
							isUser && "order-2"
						)}>
							<AvatarFallback className={cn(
								isUser 
									? "bg-primary text-primary-foreground" 
									: "bg-muted-foreground/20 text-foreground"
							)}>
								{isUser ? (
									<User className="h-4 w-4" />
								) : isStaff ? (
									<Headphones className="h-4 w-4" />
								) : (
									<Bot className="h-4 w-4" />
								)}
							</AvatarFallback>
						</Avatar>
						
						<div className={cn(
							"flex flex-col gap-1 max-w-[75%]",
							isUser ? "items-end" : "items-start"
						)}>
							{isStaff && <span className="text-xs text-muted-foreground">Staff</span>}
							<div
								className={cn(
									"rounded-2xl px-4 py-2.5 text-sm leading-relaxed shadow-sm",
									"break-words whitespace-pre-wrap",
									isUser
										? "bg-primary text-primary-foreground rounded-br-md"
										: isStaff
											? "bg-amber-50 dark:bg-amber-950/40 text-foreground rounded-bl-md border border-amber-300/60"
											: "bg-muted text-foreground rounded-bl-md border border-border/50"
								)}
							>
								{m.photoUrls?.length ? (
									<div className="flex flex-wrap gap-2">
										{m.photoUrls.map((url) => (
											<a key={url} href={withApiBase(url)} target="_blank" rel="noreferrer" className="block">
												{/* eslint-disable-next-line @next/next/no-img-element */}
												<img
													src={`${withApiBase(url)}&size=thumb`}
													alt="Photo"
													loading="lazy"
													className="max-h-60 rounded-lg object-cover"
												/>
											</a>
										))}
									</div>
								) : (
									m.hasPhoto && <span className="block">📷 Photo</span>
								)}
								{m.content}
							</div>
							{m.photoMatches && <span className="text-xs text-muted-foreground">{m.photoMatches}</span>}
							{m.credits?.map((c, i) => (
								<span key={i} className="text-xs text-muted-foreground">
									{formatCredits(c.credits)} {Number(c.credits) === 1 ? "credit" : "credits"}, {c.label}
								</span>
							))}
						</div>
					</div>
				);
			})}
		</div>
	);
}


