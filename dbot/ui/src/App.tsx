import { useState } from "react";
import { ChatPage } from "./pages/ChatPage";
import { SettingsPage } from "./pages/SettingsPage";

type Page = "chat" | "settings";

export function App() {
  const [page, setPage] = useState<Page>("chat");

  if (page === "settings") {
    return <SettingsPage onBack={() => setPage("chat")} />;
  }

  return <ChatPage onSettings={() => setPage("settings")} />;
}
