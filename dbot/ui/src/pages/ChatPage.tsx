import { useEffect, useMemo, useState } from "react";
import { ChatInput } from "../components/ChatInput";
import { CredentialDialog, detectCredentialRequired } from "../components/CredentialDialog";
import { HistorySidebar } from "../components/HistorySidebar";
import { MessageList } from "../components/MessageList";
import { useDbotChat } from "../hooks/useChat";
import { useChatHistory } from "../hooks/useChatHistory";
import { useModels } from "../hooks/useModels";

export function ChatPage({ onSettings }: { onSettings: () => void }) {
  const { models, selected, setSelected } = useModels();
  const {
    activeId,
    sessions,
    loading,
    createSession,
    switchSession,
    deleteSession,
    saveSession,
    refresh,
  } = useChatHistory();
  const { messages, sendMessage, status, error, stop, setMessages, regenerate } = useDbotChat(
    selected,
    activeId || undefined,
  );
  const [lastUserText, setLastUserText] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(true);

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

  useEffect(() => {
    if (!loading && sessions.length === 0 && !activeId) {
      createSession();
    }
  }, [activeId, createSession, loading, sessions.length]);

  useEffect(() => {
    if (!activeId) {
      return;
    }
    fetch(`/api/chats/${encodeURIComponent(activeId)}`)
      .then((response) => (response.ok ? response.json() : null))
      .then((chat: { messages?: typeof messages } | null) => {
        if (chat?.messages) {
          setMessages(chat.messages);
        } else {
          setMessages([]);
        }
      });
  }, [activeId, setMessages]);

  useEffect(() => {
    if (!activeId) {
      return;
    }
    const firstUser = messages.find((message) => message.role === "user");
    const titlePart = firstUser?.parts.find((part) => part.type === "text");
    const title = titlePart?.type === "text" ? titlePart.text.slice(0, 80) : "";
    saveSession(activeId, title, messages);
    refresh();
  }, [activeId, messages, refresh, saveSession]);

  return (
    <div className="app-shell">
      <HistorySidebar
        sessions={sessions}
        activeId={activeId}
        collapsed={!sidebarOpen}
        onToggle={() => setSidebarOpen(false)}
        onNewChat={() => createSession()}
        onSwitchSession={switchSession}
        onDeleteSession={deleteSession}
      />
      <div className="chat-layout">
        <header className="chat-header">
          {!sidebarOpen && (
            <button
              type="button"
              className="sidebar-toggle"
              onClick={() => setSidebarOpen(true)}
              aria-label="Open sidebar"
            >
              <svg
                width="18"
                height="18"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
              >
                <title>Menu</title>
                <path d="M3 12h18M3 6h18M3 18h18" />
              </svg>
            </button>
          )}
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
        <MessageList
          messages={messages}
          status={status}
          onRegenerate={status === "ready" ? () => regenerate() : undefined}
          onPromptSelect={handleSend}
        />

        {status === "error" && error && (
          <div className="error-banner" data-testid="error-banner">
            <span className="error-banner-icon">\u26A0</span>
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
          <CredentialDialog
            cred={credRequired}
            onSave={handleCredentialSave}
            onDismiss={() => {}}
          />
        )}
      </div>
    </div>
  );
}
