import type { UIMessage } from "ai";
import { ToolCallCard } from "./ToolCallCard";

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
              <ToolCallCard
                key={part.toolCallId}
                toolName={part.toolName}
                state={part.state}
                input={"input" in part ? part.input : undefined}
                output={"output" in part ? part.output : undefined}
                errorText={"errorText" in part ? (part.errorText as string) : undefined}
              />
            );
          }

          if (typeof part.type === "string" && part.type.startsWith("tool-")) {
            const p = part as {
              toolCallId: string;
              toolName?: string;
              state: string;
              input?: unknown;
              output?: unknown;
              errorText?: string;
            };
            return (
              <ToolCallCard
                key={p.toolCallId}
                toolName={p.toolName ?? part.type.slice(5)}
                state={p.state}
                input={p.input}
                output={p.output}
                errorText={p.errorText}
              />
            );
          }

          return null;
        })}
      </div>
    </div>
  );
}
