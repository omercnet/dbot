import { ChatInput } from "../components/ChatInput";
import { MessageList } from "../components/MessageList";
import { useDbotChat } from "../hooks/useChat";

export function ChatPage({ onSettings }: { onSettings: () => void }) {
  const { messages, sendMessage, status } = useDbotChat();

  return (
    <div className="chat-layout">
      <header className="chat-header">
        <span className="logo">dbot</span>
        <span className="tagline">IR Agent</span>
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
