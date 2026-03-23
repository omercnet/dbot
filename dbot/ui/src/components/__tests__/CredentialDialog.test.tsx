import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { CredentialDialog, detectCredentialRequired } from "../CredentialDialog";

describe("CredentialDialog", () => {
  const cred = { pack: "VirusTotal", required_credentials: ["apikey", "server_url"] };

  it("renders pack name and credential fields", () => {
    render(<CredentialDialog cred={cred} onSave={vi.fn()} onDismiss={vi.fn()} />);
    expect(screen.getByTestId("credential-dialog")).toBeTruthy();
    expect(screen.getByText(/VirusTotal/)).toBeTruthy();
    expect(screen.getByPlaceholderText("apikey")).toBeTruthy();
    expect(screen.getByPlaceholderText("server_url")).toBeTruthy();
  });

  it("calls onSave with credential values", async () => {
    const onSave = vi.fn().mockResolvedValue(true);
    render(<CredentialDialog cred={cred} onSave={onSave} onDismiss={vi.fn()} />);
    await userEvent.type(screen.getByPlaceholderText("apikey"), "vt-key-123");
    await userEvent.click(screen.getByText("Save & Retry"));
    expect(onSave).toHaveBeenCalledWith(
      "VirusTotal",
      expect.objectContaining({ apikey: "vt-key-123" }),
    );
  });

  it("calls onDismiss when Cancel clicked", async () => {
    const onDismiss = vi.fn();
    render(<CredentialDialog cred={cred} onSave={vi.fn()} onDismiss={onDismiss} />);
    await userEvent.click(screen.getByText("Cancel"));
    expect(onDismiss).toHaveBeenCalled();
  });
});

describe("detectCredentialRequired", () => {
  it("returns null when no credentials_required", () => {
    const msgs = [{ parts: [{ type: "text", text: "hello" }] }];
    expect(detectCredentialRequired(msgs as never)).toBeNull();
  });

  it("detects credentials_required in tool output", () => {
    const msgs = [
      {
        parts: [
          {
            type: "dynamic-tool",
            output: {
              status: "credentials_required",
              pack: "VirusTotal",
              required_credentials: ["apikey"],
            },
          },
        ],
      },
    ];
    const result = detectCredentialRequired(msgs as never);
    expect(result).toEqual({ pack: "VirusTotal", required_credentials: ["apikey"] });
  });

  it("returns null for non-credential tool outputs", () => {
    const msgs = [
      {
        parts: [
          {
            type: "dynamic-tool",
            output: { status: "success", results: [] },
          },
        ],
      },
    ];
    expect(detectCredentialRequired(msgs as never)).toBeNull();
  });
});
