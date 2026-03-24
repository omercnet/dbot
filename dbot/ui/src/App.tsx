import { lazy, Suspense, useState } from "react";
import { ChatPage } from "./pages/ChatPage";

const SettingsPage = lazy(() =>
  import("./pages/SettingsPage").then((m) => ({ default: m.SettingsPage })),
);

type Page = "chat" | "settings";

export function App() {
  const [page, setPage] = useState<Page>("chat");

  if (page === "settings") {
    return (
      <Suspense fallback={<div className="settings-loading">Loading settings\u2026</div>}>
        <SettingsPage onBack={() => setPage("chat")} />
      </Suspense>
    );
  }

  return <ChatPage onSettings={() => setPage("settings")} />;
}
