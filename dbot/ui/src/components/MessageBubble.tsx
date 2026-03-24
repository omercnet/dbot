import type { UIMessage } from "ai";
import { useState } from "react";
import { ToolCallCard } from "./ToolCallCard";

const COLLAPSE_THRESHOLD = 500;

function looksLikeJson(text: string): boolean {
  const trimmed = text.trimStart();
  return (trimmed.startsWith("{") || trimmed.startsWith("[")) && trimmed.length > 80;
}

function tryFormatJson(text: string): string | null {
  try {
    const parsed = JSON.parse(text);
    return JSON.stringify(parsed, null, 2);
  } catch {
    return null;
  }
}

function TextContent({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false);
  const isLong = text.length > COLLAPSE_THRESHOLD;
  const isJson = looksLikeJson(text);
  const formatted = isJson ? tryFormatJson(text) : null;

  if (formatted) {
    const display =
      !expanded && formatted.length > COLLAPSE_THRESHOLD
        ? `${formatted.slice(0, COLLAPSE_THRESHOLD)}…`
        : formatted;
    return (
      <div className="text-content-json">
        <pre className="json-block">{display}</pre>
        {formatted.length > COLLAPSE_THRESHOLD && (
          <button type="button" className="text-expand-btn" onClick={() => setExpanded(!expanded)}>
            {expanded ? "Collapse" : `Show all (${Math.ceil(formatted.length / 1024)}KB)`}
          </button>
        )}
      </div>
    );
  }

  if (isLong && !expanded) {
    return (
      <span>
        {text.slice(0, COLLAPSE_THRESHOLD)}…
        <button type="button" className="text-expand-btn" onClick={() => setExpanded(true)}>
          Show more ({Math.ceil(text.length / 1024)}KB)
        </button>
      </span>
    );
  }

  return <span>{text}</span>;
}

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
            return <TextContent key={i} text={part.text} />;
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
