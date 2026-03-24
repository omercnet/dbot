import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { ToolCallCard } from "../ToolCallCard";

describe("ToolCallCard", () => {
  it("shows running state", () => {
    render(<ToolCallCard toolName="search_tools" state="input-streaming" />);
    const card = screen.getByTestId("tool-card");
    expect(card).toHaveAttribute("data-state", "running");
    expect(card).toHaveTextContent("search_tools");
    expect(card).toHaveTextContent("running");
  });

  it("shows done state", () => {
    render(
      <ToolCallCard toolName="invoke_tool" state="output-available" output={{ result: "ok" }} />,
    );
    const card = screen.getByTestId("tool-card");
    expect(card).toHaveAttribute("data-state", "done");
    expect(card).toHaveTextContent("done");
  });

  it("shows error state", () => {
    render(<ToolCallCard toolName="invoke_tool" state="output-error" errorText="failed" />);
    expect(screen.getByTestId("tool-card")).toHaveAttribute("data-state", "error");
  });

  it("expands to show args on click", async () => {
    render(
      <ToolCallCard
        toolName="search_tools"
        state="output-available"
        input={{ query: "firewall" }}
        output={{ results: [] }}
      />,
    );
    await userEvent.click(screen.getByRole("button"));
    expect(screen.getByText("Arguments")).toBeTruthy();
    expect(screen.getByText(/firewall/)).toBeTruthy();
  });

  it("redacts credential values in args", async () => {
    render(
      <ToolCallCard
        toolName="invoke_tool"
        state="output-available"
        input={{ api_key: "sk-secret-123", query: "hello" }}
        output={{}}
      />,
    );
    await userEvent.click(screen.getByRole("button"));
    expect(screen.queryByText(/sk-secret/)).toBeNull();
    expect(screen.getByText(/\u2022\u2022\u2022\u2022/)).toBeTruthy();
  });

  it("redacts credential values in output", async () => {
    render(
      <ToolCallCard
        toolName="invoke_tool"
        state="output-available"
        input={{}}
        output={{ password: "my-secret", data: "visible" }}
      />,
    );
    await userEvent.click(screen.getByRole("button"));
    expect(screen.queryByText(/my-secret/)).toBeNull();
    expect(screen.getByText(/visible/)).toBeTruthy();
  });

  it("does not expand when no details", async () => {
    render(<ToolCallCard toolName="search_tools" state="input-streaming" />);
    const btn = screen.getByRole("button");
    expect(btn).toBeDisabled();
  });
});
