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
  const [loading, setLoading] = useState(true);

  const fetchSessions = useCallback(async () => {
    try {
      const r = await fetch("/api/chats");
      if (!r.ok) return [];
      const data: ApiChatSession[] = await r.json();
      return data.map((s) => ({
        id: s.id,
        title: s.title,
        createdAt: s.created_at,
        messageCount: s.message_count,
      }));
    } catch {
      return [];
    }
  }, []);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const mapped = await fetchSessions();
      setSessions(mapped);
      return mapped;
    } finally {
      setLoading(false);
    }
  }, [fetchSessions]);

  useEffect(() => {
    fetchSessions()
      .then((mapped) => {
        setSessions(mapped);
        setActiveId((cur) => cur || (mapped.length > 0 ? mapped[0].id : ""));
      })
      .finally(() => setLoading(false));
  }, []); // biome-ignore lint/correctness/useExhaustiveDependencies: only fetch on mount

  const createSession = useCallback((): string => {
    const id = crypto.randomUUID();
    setActiveId(id);
    fetch(`/api/chats/${encodeURIComponent(id)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: "", messages: [] }),
    }).then(() => refresh());
    return id;
  }, [refresh]);

  const switchSession = useCallback((id: string) => {
    setActiveId(id);
  }, []);

  const deleteSession = useCallback(
    (id: string) => {
      fetch(`/api/chats/${encodeURIComponent(id)}`, { method: "DELETE" }).then(() => {
        setActiveId((cur) => (cur === id ? "" : cur));
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
