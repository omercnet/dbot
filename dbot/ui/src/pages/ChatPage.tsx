import { ChatInput } from "../components/ChatInput";
import { MessageList } from "../components/MessageList";
import { useDbotChat } from "../hooks/useChat";
import { useModels } from "../hooks/useModels";

export function ChatPage({ onSettings }: { onSettings: () => void }) {
  const { models, selected, setSelected } = useModels();
  const { messages, sendMessage, status } = useDbotChat(selected);

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
      <ChatInput onSend={(text) => sendMessage({ text })} status={status} />
    </div>
  );
}
