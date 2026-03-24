import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  const { messages, sendMessage, status, error, stop, setMessages, regenerate } =
    useDbotChat(selected);
  const [lastUserText, setLastUserText] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [showCredDialog, setShowCredDialog] = useState(false);
  const [dismissedCredPack, setDismissedCredPack] = useState<string | null>(null);

  const credRequired = useMemo(
    () => (status === "ready" || status === "error" ? detectCredentialRequired(messages) : null),
    [messages, status],
  );

  const isCredInvalid = credRequired?.status === "credentials_invalid";
  const credDismissed = credRequired && dismissedCredPack === credRequired.pack;
  const showCredModal =
    credRequired &&
    !credDismissed &&
    (credRequired.status === "credentials_required" || showCredDialog);

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

  const prevActiveId = useRef(activeId);
  useEffect(() => {
    if (!activeId) return;
    if (prevActiveId.current !== activeId) {
      setMessages([]);
      prevActiveId.current = activeId;
    }
    let stale = false;
    fetch(`/api/chats/${encodeURIComponent(activeId)}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((chat: { messages?: typeof messages } | null) => {
        if (stale) return;
        setMessages(chat?.messages?.length ? chat.messages : []);
      });
    return () => {
      stale = true;
    };
  }, [activeId, setMessages]);

  const saveTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const saveRef = useRef(saveSession);
  saveRef.current = saveSession;

  const debouncedSave = useCallback(
    (id: string, msgs: typeof messages) => {
      clearTimeout(saveTimer.current);
      saveTimer.current = setTimeout(() => {
        const firstUser = msgs.find((m) => m.role === "user");
        const titlePart = firstUser?.parts.find((p) => p.type === "text");
        const title = titlePart?.type === "text" ? titlePart.text.slice(0, 80) : "";
        saveRef.current(id, title, msgs);
        refresh();
      }, 800);
    },
    [refresh],
  );

  useEffect(() => {
    if (!activeId || messages.length === 0) return;
    debouncedSave(activeId, messages);
  }, [activeId, messages, debouncedSave]);

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
            <span className="error-banner-icon">{"\u26A0"}</span>
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

        {isCredInvalid && credRequired && (
          <div className="error-banner cred-callout" data-testid="cred-callout">
            <span className="error-banner-icon">{"\u{1F511}"}</span>
            <span className="error-banner-text">
              <strong>{credRequired.pack}</strong> credentials failed
              {credRequired.error ? `: ${credRequired.error}` : "."}
            </span>
            <button type="button" className="btn btn-sm" onClick={() => setShowCredDialog(true)}>
              Reconfigure
            </button>
          </div>
        )}

        <ChatInput onSend={handleSend} status={status} onStop={stop} />

        {showCredModal && credRequired && (
          <CredentialDialog
            cred={credRequired}
            onSave={handleCredentialSave}
            onDismiss={() => {
              setShowCredDialog(false);
              setDismissedCredPack(credRequired.pack);
            }}
          />
        )}
      </div>
    </div>
  );
}
