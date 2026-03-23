import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport, type UIMessage } from "ai";
import { useMemo } from "react";

export type ChatStatus = "submitted" | "streaming" | "ready" | "error";

export type DbotChat = {
  messages: UIMessage[];
  status: ChatStatus;
  error: Error | undefined;
  sendMessage: (msg: { text: string }) => void;
  setMessages: (messages: UIMessage[] | ((prev: UIMessage[]) => UIMessage[])) => void;
  stop: () => void;
};

export function useDbotChat(modelId?: string): DbotChat {
  const transport = useMemo(
    () =>
      new DefaultChatTransport({
        api: "/api/chat",
        body: modelId ? { model: modelId } : undefined,
      }),
    [modelId],
  );

  const { messages, sendMessage, status, error, setMessages, stop } = useChat({ transport });
  return { messages, sendMessage, status, error, setMessages, stop };
}
