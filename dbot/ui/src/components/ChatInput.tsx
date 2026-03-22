import { type KeyboardEvent, useRef, useState } from "react";
import type { ChatStatus } from "../hooks/useChat";

export function ChatInput({
  onSend,
  status,
}: {
  onSend: (text: string) => void;
  status: ChatStatus;
}) {
  const [input, setInput] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const busy = status === "submitted" || status === "streaming";

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  function submit() {
    const text = input.trim();
    if (!text || busy) return;
    onSend(text);
    setInput("");
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
  }

  function handleInput(e: React.ChangeEvent<HTMLTextAreaElement>) {
    setInput(e.target.value);
    const el = e.target;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 120)}px`;
  }

  return (
    <div className="chat-input-bar">
      <div className="chat-input-wrap">
        <textarea
          ref={textareaRef}
          data-testid="chat-input"
          rows={1}
          value={input}
          onChange={handleInput}
          onKeyDown={handleKeyDown}
          placeholder={busy ? "Thinking\u2026" : "Describe the incident or ask a question\u2026"}
          disabled={busy}
        />
        <button
          type="button"
          className="send-btn"
          data-testid="send-button"
          onClick={submit}
          disabled={busy || !input.trim()}
          aria-label="Send message"
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.5"
            aria-hidden="true"
          >
            <title>Send</title>
            <path d="M5 12h14M12 5l7 7-7 7" />
          </svg>
        </button>
      </div>
    </div>
  );
}
