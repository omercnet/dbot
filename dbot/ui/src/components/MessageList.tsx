import type { UIMessage } from "ai";
import { useEffect, useRef } from "react";
import type { ChatStatus } from "../hooks/useChat";
import { MessageBubble } from "./MessageBubble";

export function MessageList({ messages, status }: { messages: UIMessage[]; status: ChatStatus }) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const isStreaming = status === "streaming";

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }); // biome-ignore lint/correctness/useExhaustiveDependencies: scroll on every render is intentional

  if (messages.length === 0) {
    return (
      <div className="message-list" data-testid="message-list">
        <div className="empty-state">
          <span className="logo-large">dbot</span>
          <p>Ask me to investigate a security incident.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="message-list" data-testid="message-list">
      {messages.map((msg, i) => (
        <MessageBubble
          key={msg.id}
          message={msg}
          isStreaming={isStreaming && i === messages.length - 1 && msg.role === "assistant"}
        />
      ))}
      <div ref={bottomRef} />
    </div>
  );
}
