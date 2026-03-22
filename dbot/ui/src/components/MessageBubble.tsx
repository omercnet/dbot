import type { UIMessage } from "ai";
import { ToolCallBadge } from "./ToolCallBadge";

export function MessageBubble({
  message,
  isStreaming,
}: {
  message: UIMessage;
  isStreaming: boolean;
}) {
  const isUser = message.role === "user";
  const isAssistant = message.role === "assistant";

  return (
    <div className={`message-row ${message.role}`} data-testid="message-bubble">
      <span className="message-role">{isUser ? "You" : "dbot"}</span>
      <div className={`message-bubble ${isStreaming && isAssistant ? "streaming-cursor" : ""}`}>
        {message.parts.map((part, i) => {
          if (part.type === "text") {
            // biome-ignore lint/suspicious/noArrayIndexKey: text parts lack stable IDs
            return <span key={i}>{part.text}</span>;
          }

          if (part.type === "dynamic-tool") {
            return (
              <ToolCallBadge key={part.toolCallId} toolName={part.toolName} state={part.state} />
            );
          }

          // Tool parts with typed names (tool-{name}) also have the same shape
          if (typeof part.type === "string" && part.type.startsWith("tool-")) {
            const toolPart = part as { toolCallId: string; toolName?: string; state: string };
            return (
              <ToolCallBadge
                key={toolPart.toolCallId}
                toolName={toolPart.toolName ?? part.type.slice(5)}
                state={toolPart.state}
              />
            );
          }

          return null;
        })}
      </div>
    </div>
  );
}
