type ToolState =
  | "input-streaming"
  | "input-available"
  | "output-available"
  | "output-error"
  | string;

function stateLabel(state: ToolState): { icon: string; text: string; badge: string } {
  switch (state) {
    case "input-streaming":
    case "input-available":
      return { icon: "spinner", text: "running\u2026", badge: "running" };
    case "output-available":
      return { icon: "\u2713", text: "done", badge: "done" };
    case "output-error":
      return { icon: "\u2715", text: "error", badge: "error" };
    default:
      return { icon: "\u2026", text: state, badge: "running" };
  }
}

export function ToolCallBadge({ toolName, state }: { toolName: string; state: ToolState }) {
  const { icon, text, badge } = stateLabel(state);

  return (
    <span className="tool-badge" data-state={badge} data-testid="tool-badge">
      {icon === "spinner" ? <span className="spinner" /> : <span>{icon}</span>}
      <span>{toolName}</span>
      <span>{text}</span>
    </span>
  );
}
