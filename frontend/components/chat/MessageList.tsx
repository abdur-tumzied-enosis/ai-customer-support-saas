import { cn } from "@/lib/utils";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { User, Bot } from "lucide-react";

export type ChatMessage = {
	id: string;
	role: "user" | "assistant" | "system";
	content: string;
};

type MessageListProps = {
	messages: ChatMessage[];
};

export default function MessageList({ messages }: MessageListProps) {
	return (
		<div className="space-y-4 pb-2">
			{messages.map((m, idx) => {
				const isUser = m.role === "user";
				const isAssistant = m.role === "assistant";
				
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
								) : (
									<Bot className="h-4 w-4" />
								)}
							</AvatarFallback>
						</Avatar>
						
						<div className={cn(
							"flex flex-col gap-1 max-w-[75%]",
							isUser ? "items-end" : "items-start"
						)}>
							<div
								className={cn(
									"rounded-2xl px-4 py-2.5 text-sm leading-relaxed shadow-sm",
									"break-words whitespace-pre-wrap",
									isUser
										? "bg-primary text-primary-foreground rounded-br-md"
										: "bg-muted text-foreground rounded-bl-md border border-border/50"
								)}
							>
								{m.content}
							</div>
						</div>
					</div>
				);
			})}
		</div>
	);
}


