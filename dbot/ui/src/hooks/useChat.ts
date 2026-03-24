import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport, type UIMessage } from "ai";
import { useMemo } from "react";

export type ChatStatus = "submitted" | "streaming" | "ready" | "error";

export type DbotChat = {
  id: ReturnType<typeof useChat>["id"];
  messages: UIMessage[];
  status: ChatStatus;
  error: Error | undefined;
  sendMessage: (msg: { text: string }) => void;
  setMessages: (messages: UIMessage[] | ((prev: UIMessage[]) => UIMessage[])) => void;
  stop: () => void;
  regenerate: ReturnType<typeof useChat>["regenerate"];
};

export function useDbotChat(modelId?: string, chatId?: string): DbotChat {
  const transport = useMemo(
    () =>
      new DefaultChatTransport({
        api: "/api/chat",
        body: modelId ? { model: modelId } : undefined,
      }),
    [modelId],
  );

  const { id, messages, sendMessage, status, error, setMessages, stop, regenerate } = useChat({
    id: chatId,
    transport,
  });
  return { id, messages, sendMessage, status, error, setMessages, stop, regenerate };
}
