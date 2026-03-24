import type { UIMessage } from "ai";
import { useState } from "react";
import { MarkdownContent } from "./MarkdownContent";
import { ToolCallCard } from "./ToolCallCard";

function CopyButton({ getText }: { getText: () => string }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = () => {
    navigator.clipboard.writeText(getText());
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };
  return (
    <button type="button" className="action-btn" onClick={handleCopy} title="Copy">
      {copied ? (
        <svg
          className="icon"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.5"
        >
          <title>Copied</title>
          <path d="M20 6L9 17l-5-5" />
        </svg>
      ) : (
        <svg className="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <title>Copy</title>
          <rect x="9" y="9" width="13" height="13" rx="2" />
          <path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1" />
        </svg>
      )}
    </button>
  );
}

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
  onRegenerate,
}: {
  message: UIMessage;
  isStreaming: boolean;
  onRegenerate?: () => void;
}) {
  const isUser = message.role === "user";
  const isAssistant = message.role === "assistant";

  const getTextContent = () =>
    message.parts
      .filter((p) => p.type === "text")
      .map((p) => ("text" in p ? p.text : ""))
      .join("\n");

  return (
    <div className={`message-row ${message.role}`} data-testid="message-bubble">
      <span className="message-role">{isUser ? "You" : "dbot"}</span>
      <div className={`message-bubble ${isStreaming && isAssistant ? "streaming-cursor" : ""}`}>
        {message.parts.map((part, i) => {
          if (part.type === "text") {
            if (isAssistant) {
              // biome-ignore lint/suspicious/noArrayIndexKey: text parts lack stable IDs
              return <MarkdownContent key={i} text={part.text} />;
            }
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
      <div className="message-actions">
        <CopyButton getText={getTextContent} />
        {isAssistant && onRegenerate && (
          <button
            type="button"
            className="action-btn"
            onClick={onRegenerate}
            disabled={isStreaming}
            title="Regenerate"
          >
            <svg
              className="icon"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <title>Regenerate</title>
              <path d="M1 4v6h6M23 20v-6h-6" />
              <path d="M20.49 9A9 9 0 005.64 5.64L1 10m22 4l-4.64 4.36A9 9 0 013.51 15" />
            </svg>
          </button>
        )}
      </div>
    </div>
  );
}
