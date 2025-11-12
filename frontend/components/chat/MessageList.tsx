import { cn } from "@/lib/utils";

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
		<div className="space-y-3">
			{messages.map((m) => (
				<div key={m.id} className="flex">
					<div
						className={cn(
							"rounded-lg px-3 py-2 text-sm max-w-[80%]",
							m.role === "user"
								? "ml-auto bg-primary text-primary-foreground"
								: "mr-auto bg-muted text-muted-foreground"
						)}
					>
						{m.content}
					</div>
				</div>
			))}
		</div>
	);
}


