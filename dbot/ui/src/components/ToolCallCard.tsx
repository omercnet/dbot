import { useState } from "react";

type ToolState =
  | "input-streaming"
  | "input-available"
  | "output-available"
  | "output-error"
  | string;

const REDACT_PATTERNS = /api[_-]?key|password|secret|token|credential|auth/i;

function redactValue(key: string, value: unknown): unknown {
  if (typeof value === "string" && REDACT_PATTERNS.test(key)) {
    return value.length > 0 ? "••••••••" : "";
  }
  if (typeof value === "object" && value !== null) {
    return redactObject(value as Record<string, unknown>);
  }
  return value;
}

function redactObject(obj: Record<string, unknown>): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(obj)) {
    result[k] = redactValue(k, v);
  }
  return result;
}

function stateInfo(state: ToolState): { icon: string; label: string; badge: string } {
  switch (state) {
    case "input-streaming":
    case "input-available":
      return { icon: "spinner", label: "running\u2026", badge: "running" };
    case "output-available":
      return { icon: "\u2713", label: "done", badge: "done" };
    case "output-error":
      return { icon: "\u2715", label: "error", badge: "error" };
    default:
      return { icon: "\u2026", label: state, badge: "running" };
  }
}

function formatArgs(input: unknown): string {
  if (!input || typeof input !== "object") return "";
  const redacted = redactObject(input as Record<string, unknown>);
  return JSON.stringify(redacted, null, 2);
}

function formatOutput(output: unknown, errorText?: string): string {
  if (errorText) return errorText;
  if (!output) return "";
  if (typeof output === "string") return output;
  const redacted =
    typeof output === "object" ? redactObject(output as Record<string, unknown>) : output;
  return JSON.stringify(redacted, null, 2);
}

export function ToolCallCard({
  toolName,
  state,
  input,
  output,
  errorText,
}: {
  toolName: string;
  state: ToolState;
  input?: unknown;
  output?: unknown;
  errorText?: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const { icon, label, badge } = stateInfo(state);
  const hasDetails = input || output || errorText;
  const isDone = state === "output-available" || state === "output-error";

  return (
    <div className="tool-card" data-state={badge} data-testid="tool-card">
      <button
        type="button"
        className="tool-card-header"
        onClick={() => hasDetails && setExpanded(!expanded)}
        disabled={!hasDetails}
      >
        <span className="tool-card-icon">
          {icon === "spinner" ? <span className="spinner" /> : icon}
        </span>
        <span className="tool-card-name">{toolName}</span>
        <span className="tool-card-status">{label}</span>
        {hasDetails && <span className="tool-card-chevron">{expanded ? "\u25B2" : "\u25BC"}</span>}
      </button>

      {expanded && (
        <div className="tool-card-body">
          {input != null && (
            <div className="tool-card-section">
              <span className="tool-card-label">Arguments</span>
              <pre className="tool-card-pre">{formatArgs(input)}</pre>
            </div>
          )}
          {isDone && (output || errorText) && (
            <div className="tool-card-section">
              <span className="tool-card-label">
                {state === "output-error" ? "Error" : "Result"}
              </span>
              <pre className={`tool-card-pre ${state === "output-error" ? "tool-card-error" : ""}`}>
                {formatOutput(output, errorText)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
