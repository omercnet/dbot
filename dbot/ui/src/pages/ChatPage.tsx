import { useMemo, useState } from "react";
import { ChatInput } from "../components/ChatInput";
import { CredentialDialog, detectCredentialRequired } from "../components/CredentialDialog";
import { MessageList } from "../components/MessageList";
import { useDbotChat } from "../hooks/useChat";
import { useModels } from "../hooks/useModels";

export function ChatPage({ onSettings }: { onSettings: () => void }) {
  const { models, selected, setSelected } = useModels();
  const { messages, sendMessage, status, error, stop } = useDbotChat(selected);
  const [lastUserText, setLastUserText] = useState("");

  const credRequired = useMemo(
    () => (status === "ready" || status === "error" ? detectCredentialRequired(messages) : null),
    [messages, status],
  );

  function handleSend(text: string) {
    setLastUserText(text);
    sendMessage({ text });
  }

  async function handleCredentialSave(pack: string, credentials: Record<string, string>) {
    const entries = Object.entries(credentials).filter(([, v]) => v);
    for (const [name, value] of entries) {
      await fetch(`/api/settings/credentials/${encodeURIComponent(pack)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [name]: value }),
      });
    }
    if (lastUserText) {
      sendMessage({ text: lastUserText });
    }
    return true;
  }

  return (
    <div className="chat-layout">
      <header className="chat-header">
        <span className="logo">dbot</span>
        {models.length > 0 && (
          <select
            className="model-select"
            value={selected}
            onChange={(e) => setSelected(e.target.value)}
            disabled={status === "streaming" || status === "submitted"}
          >
            {models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
              </option>
            ))}
          </select>
        )}
        <span className="status-dot" data-status={status} title={status} />
        <button type="button" className="btn btn-sm" onClick={onSettings}>
          Settings
        </button>
      </header>
      <MessageList messages={messages} status={status} />

      {status === "error" && error && (
        <div className="error-banner" data-testid="error-banner">
          <span className="error-banner-icon">⚠</span>
          <span className="error-banner-text">{error.message || "Something went wrong"}</span>
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => lastUserText && sendMessage({ text: lastUserText })}
          >
            Retry
          </button>
        </div>
      )}

      <ChatInput onSend={handleSend} status={status} onStop={stop} />

      {credRequired && (
        <CredentialDialog cred={credRequired} onSave={handleCredentialSave} onDismiss={() => {}} />
      )}
    </div>
  );
}
