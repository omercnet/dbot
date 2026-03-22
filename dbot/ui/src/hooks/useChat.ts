import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport, type UIMessage } from "ai";

export type ChatStatus = "submitted" | "streaming" | "ready" | "error";

export type DbotChat = {
  messages: UIMessage[];
  status: ChatStatus;
  error: Error | undefined;
  sendMessage: (msg: { text: string }) => void;
  setMessages: (messages: UIMessage[] | ((prev: UIMessage[]) => UIMessage[])) => void;
  stop: () => void;
};

const transport = new DefaultChatTransport({ api: "/api/chat" });

export function useDbotChat(): DbotChat {
  const { messages, sendMessage, status, error, setMessages, stop } = useChat({ transport });
  return { messages, sendMessage, status, error, setMessages, stop };
}
