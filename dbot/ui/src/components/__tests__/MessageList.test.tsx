import { render, screen } from "@testing-library/react";
import type { UIMessage } from "ai";
import { describe, expect, it } from "vitest";
import { MessageList } from "../MessageList";

function makeMessages(count: number): UIMessage[] {
  return Array.from({ length: count }, (_, i) => ({
    id: `msg-${i}`,
    role: (i % 2 === 0 ? "user" : "assistant") as UIMessage["role"],
    parts: [{ type: "text" as const, text: `Message ${i}` }],
  }));
}

describe("MessageList", () => {
  it("shows empty state when no messages", () => {
    render(<MessageList messages={[]} status="ready" />);
    expect(screen.getByText("dbot")).toBeTruthy();
    expect(screen.getByText(/Ask me/)).toBeTruthy();
  });

  it("renders all messages", () => {
    const msgs = makeMessages(4);
    render(<MessageList messages={msgs} status="ready" />);
    const bubbles = screen.getAllByTestId("message-bubble");
    expect(bubbles).toHaveLength(4);
  });

  it("marks last assistant message as streaming when status is streaming", () => {
    const msgs: UIMessage[] = [
      { id: "1", role: "user", parts: [{ type: "text", text: "hi" }] },
      { id: "2", role: "assistant", parts: [{ type: "text", text: "hello" }] },
    ];
    const { container } = render(<MessageList messages={msgs} status="streaming" />);
    expect(container.querySelector(".streaming-cursor")).toBeTruthy();
  });

  it("has data-testid on container", () => {
    render(<MessageList messages={[]} status="ready" />);
    expect(screen.getByTestId("message-list")).toBeTruthy();
  });
});
