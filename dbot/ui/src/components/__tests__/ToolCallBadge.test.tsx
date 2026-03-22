import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ToolCallBadge } from "../ToolCallBadge";

describe("ToolCallBadge", () => {
  it("shows running state for input-streaming", () => {
    render(<ToolCallBadge toolName="invoke_tool" state="input-streaming" />);
    const badge = screen.getByTestId("tool-badge");
    expect(badge).toHaveAttribute("data-state", "running");
    expect(badge).toHaveTextContent("running");
    expect(badge).toHaveTextContent("invoke_tool");
  });

  it("shows running state for input-available", () => {
    render(<ToolCallBadge toolName="search_tools" state="input-available" />);
    expect(screen.getByTestId("tool-badge")).toHaveAttribute("data-state", "running");
  });

  it("shows done state for output-available", () => {
    render(<ToolCallBadge toolName="search_tools" state="output-available" />);
    const badge = screen.getByTestId("tool-badge");
    expect(badge).toHaveAttribute("data-state", "done");
    expect(badge).toHaveTextContent("done");
  });

  it("shows error state for output-error", () => {
    render(<ToolCallBadge toolName="invoke_tool" state="output-error" />);
    const badge = screen.getByTestId("tool-badge");
    expect(badge).toHaveAttribute("data-state", "error");
    expect(badge).toHaveTextContent("error");
  });

  it("shows running for unknown states", () => {
    render(<ToolCallBadge toolName="x" state="some-future-state" />);
    expect(screen.getByTestId("tool-badge")).toHaveAttribute("data-state", "running");
  });
});
