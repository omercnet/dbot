import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ChatInput } from "../ChatInput";

describe("ChatInput", () => {
  it("calls onSend when Enter is pressed", async () => {
    const onSend = vi.fn();
    render(<ChatInput onSend={onSend} status="ready" />);
    const textarea = screen.getByTestId("chat-input");
    await userEvent.type(textarea, "hello{Enter}");
    expect(onSend).toHaveBeenCalledWith("hello");
  });

  it("does NOT send on Shift+Enter", async () => {
    const onSend = vi.fn();
    render(<ChatInput onSend={onSend} status="ready" />);
    const textarea = screen.getByTestId("chat-input");
    await userEvent.type(textarea, "line1{Shift>}{Enter}{/Shift}line2");
    expect(onSend).not.toHaveBeenCalled();
  });

  it("does NOT send empty input", async () => {
    const onSend = vi.fn();
    render(<ChatInput onSend={onSend} status="ready" />);
    const textarea = screen.getByTestId("chat-input");
    await userEvent.type(textarea, "   {Enter}");
    expect(onSend).not.toHaveBeenCalled();
  });

  it("disables textarea when streaming", () => {
    render(<ChatInput onSend={vi.fn()} status="streaming" />);
    expect(screen.getByTestId("chat-input")).toBeDisabled();
  });

  it("disables textarea when submitted", () => {
    render(<ChatInput onSend={vi.fn()} status="submitted" />);
    expect(screen.getByTestId("chat-input")).toBeDisabled();
  });

  it("send button disabled when empty", () => {
    render(<ChatInput onSend={vi.fn()} status="ready" />);
    expect(screen.getByTestId("send-button")).toBeDisabled();
  });

  it("send button enabled when input has text", async () => {
    render(<ChatInput onSend={vi.fn()} status="ready" />);
    await userEvent.type(screen.getByTestId("chat-input"), "test");
    expect(screen.getByTestId("send-button")).not.toBeDisabled();
  });

  it("clears input after sending", async () => {
    render(<ChatInput onSend={vi.fn()} status="ready" />);
    const textarea = screen.getByTestId("chat-input");
    await userEvent.type(textarea, "hello{Enter}");
    expect(textarea).toHaveValue("");
  });
});
