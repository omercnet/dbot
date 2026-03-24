import type { UIMessage } from "ai";
import { useCallback, useEffect, useState } from "react";

export type ChatSession = {
  id: string;
  title: string;
  createdAt: string;
  messageCount: number;
};

export type ChatHistory = {
  sessions: ChatSession[];
  activeId: string;
  loading: boolean;
  createSession: () => string;
  switchSession: (id: string) => void;
  deleteSession: (id: string) => void;
  saveSession: (id: string, title: string, messages: UIMessage[]) => void;
  refresh: () => void;
};

type ApiChatSession = {
  id: string;
  title: string;
  created_at: string;
  message_count: number;
};

export function useChatHistory(): ChatHistory {
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [activeId, setActiveId] = useState<string>("");
  const [loading, setLoading] = useState<boolean>(false);

  const refresh = useCallback(() => {
    setLoading(true);
    fetch("/api/chats")
      .then((response) => (response.ok ? response.json() : []))
      .then((data: ApiChatSession[]) => {
        const mapped = data.map((session) => ({
          id: session.id,
          title: session.title,
          createdAt: session.created_at,
          messageCount: session.message_count,
        }));
        setSessions(mapped);
        if (!activeId && mapped.length > 0) {
          setActiveId(mapped[0].id);
        }
      })
      .finally(() => setLoading(false));
  }, [activeId]);

  const createSession = useCallback((): string => {
    const id = crypto.randomUUID();
    setActiveId(id);
    fetch(`/api/chats/${encodeURIComponent(id)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: "", messages: [] }),
    }).finally(() => refresh());
    return id;
  }, [refresh]);

  const switchSession = useCallback((id: string) => {
    setActiveId(id);
  }, []);

  const deleteSession = useCallback(
    (id: string) => {
      fetch(`/api/chats/${encodeURIComponent(id)}`, {
        method: "DELETE",
      }).finally(() => {
        setActiveId((current) => (current === id ? "" : current));
        refresh();
      });
    },
    [refresh],
  );

  const saveSession = useCallback((id: string, title: string, messages: UIMessage[]) => {
    fetch(`/api/chats/${encodeURIComponent(id)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title, messages }),
    });
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return {
    sessions,
    activeId,
    loading,
    createSession,
    switchSession,
    deleteSession,
    saveSession,
    refresh,
  };
}
