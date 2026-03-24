import type { UIMessage } from "ai";
import { useEffect, useRef } from "react";
import type { ChatStatus } from "../hooks/useChat";
import { MessageBubble } from "./MessageBubble";
import { TypingIndicator } from "./TypingIndicator";

const SUGGESTED_PROMPTS = [
  {
    icon: "\u{1F50D}",
    label: "Investigate suspicious IP",
    text: "Investigate suspicious login attempts from 203.0.113.42",
  },
  {
    icon: "\u{1F6E1}\uFE0F",
    label: "Triage a detection alert",
    text: "Triage this CrowdStrike detection: Possible lateral movement via PsExec",
  },
  {
    icon: "\u{1F4CB}",
    label: "Analyze file hash",
    text: "Check the reputation of SHA256 hash e3b0c44298fc1c149afbf4c8996fb924",
  },
  {
    icon: "\u{1F310}",
    label: "Domain reputation check",
    text: "Look up the reputation and WHOIS info for suspicious-domain.xyz",
  },
];

export function MessageList({
  messages,
  status,
  onRegenerate,
  onPromptSelect,
}: {
  messages: UIMessage[];
  status: ChatStatus;
  onRegenerate?: () => void;
  onPromptSelect?: (text: string) => void;
}) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const isStreaming = status === "streaming";

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: isStreaming ? "auto" : "smooth" });
  }); // biome-ignore lint/correctness/useExhaustiveDependencies: scroll on every render is intentional

  if (messages.length === 0) {
    return (
      <div className="message-list" data-testid="message-list">
        <div className="empty-state">
          <div className="empty-logo">dbot</div>
          <p className="empty-tagline">
            Your AI security analyst. Investigate threats, enrich IOCs, triage alerts.
          </p>
          <div className="prompt-grid">
            {SUGGESTED_PROMPTS.map((p) => (
              <button
                type="button"
                key={p.label}
                className="prompt-chip"
                onClick={() => onPromptSelect?.(p.text)}
              >
                <span className="prompt-icon">{p.icon}</span>
                <span className="prompt-label">{p.label}</span>
              </button>
            ))}
          </div>
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
          onRegenerate={
            msg.role === "assistant" && i === messages.length - 1 ? onRegenerate : undefined
          }
        />
      ))}
      {status === "submitted" && <TypingIndicator />}
      <div ref={bottomRef} />
    </div>
  );
}
