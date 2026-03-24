import type { ChatSession } from "../hooks/useChatHistory";

function relativeTime(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 30) return `${days}d ago`;
  return new Date(dateStr).toLocaleDateString();
}

export function HistorySidebar({
  sessions,
  activeId,
  collapsed,
  onToggle,
  onNewChat,
  onSwitchSession,
  onDeleteSession,
}: {
  sessions: ChatSession[];
  activeId: string;
  collapsed: boolean;
  onToggle: () => void;
  onNewChat: () => void;
  onSwitchSession: (id: string) => void;
  onDeleteSession: (id: string) => void;
}) {
  return (
    <aside className={`sidebar ${collapsed ? "sidebar-collapsed" : ""}`}>
      <div className="sidebar-header">
        <button
          type="button"
          className="sidebar-toggle"
          onClick={onToggle}
          aria-label="Close sidebar"
        >
          <svg
            width="18"
            height="18"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
          >
            <title>Close</title>
            <path d="M18 6L6 18M6 6l12 12" />
          </svg>
        </button>
        <button type="button" className="sidebar-new-chat" onClick={onNewChat}>
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
          >
            <title>New</title>
            <path d="M12 5v14M5 12h14" />
          </svg>
          New chat
        </button>
      </div>
      <div className="sidebar-sessions">
        {sessions.length === 0 && <div className="sidebar-empty">No conversations yet</div>}
        {sessions.map((s) => (
          <button
            type="button"
            key={s.id}
            className={`sidebar-item ${activeId === s.id ? "active" : ""}`}
            onClick={() => onSwitchSession(s.id)}
          >
            <div className="sidebar-item-content">
              <div className="sidebar-item-title">{s.title || "New conversation"}</div>
              <div className="sidebar-item-date">{relativeTime(s.createdAt)}</div>
            </div>
            <button
              type="button"
              className="sidebar-item-delete"
              onClick={(e) => {
                e.stopPropagation();
                onDeleteSession(s.id);
              }}
              aria-label="Delete chat"
            >
              <svg
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
              >
                <title>Delete</title>
                <path d="M3 6h18M8 6V4a2 2 0 012-2h4a2 2 0 012 2v2m3 0v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6h14" />
              </svg>
            </button>
          </button>
        ))}
      </div>
    </aside>
  );
}
