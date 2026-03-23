import { render, screen } from "@testing-library/react";
import type { UIMessage } from "ai";
import { describe, expect, it } from "vitest";
import { MessageBubble } from "../MessageBubble";

function makeMessage(overrides: Partial<UIMessage> & { role: UIMessage["role"] }): UIMessage {
  const { role, ...rest } = overrides;
  return {
    id: "msg-1",
    role,
    parts: [{ type: "text" as const, text: "Hello" }],
    ...rest,
  };
}

describe("MessageBubble", () => {
  it("renders user message with user class", () => {
    const msg = makeMessage({ role: "user" });
    render(<MessageBubble message={msg} isStreaming={false} />);
    const bubble = screen.getByTestId("message-bubble");
    expect(bubble).toHaveClass("user");
    expect(bubble).toHaveTextContent("You");
    expect(bubble).toHaveTextContent("Hello");
  });

  it("renders assistant message with assistant class", () => {
    const msg = makeMessage({ role: "assistant" });
    render(<MessageBubble message={msg} isStreaming={false} />);
    const bubble = screen.getByTestId("message-bubble");
    expect(bubble).toHaveClass("assistant");
    expect(bubble).toHaveTextContent("dbot");
  });

  it("shows streaming cursor when streaming", () => {
    const msg = makeMessage({ role: "assistant" });
    const { container } = render(<MessageBubble message={msg} isStreaming={true} />);
    expect(container.querySelector(".streaming-cursor")).toBeTruthy();
  });

  it("no streaming cursor when not streaming", () => {
    const msg = makeMessage({ role: "assistant" });
    const { container } = render(<MessageBubble message={msg} isStreaming={false} />);
    expect(container.querySelector(".streaming-cursor")).toBeFalsy();
  });

  it("renders tool call badge for dynamic-tool parts", () => {
    const msg = makeMessage({
      role: "assistant",
      parts: [
        {
          type: "dynamic-tool" as const,
          toolCallId: "tc-1",
          toolName: "search_tools",
          state: "output-available" as const,
          input: {},
          output: {},
        },
      ],
    });
    render(<MessageBubble message={msg} isStreaming={false} />);
    expect(screen.getByTestId("tool-card")).toHaveTextContent("search_tools");
    expect(screen.getByTestId("tool-card")).toHaveAttribute("data-state", "done");
  });
});
