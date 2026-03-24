export function TypingIndicator() {
  return (
    <div className="message-row assistant" data-testid="typing-indicator">
      <span className="message-role">dbot</span>
      <div className="message-bubble">
        <div className="typing-dots">
          <span className="typing-dot" />
          <span className="typing-dot" />
          <span className="typing-dot" />
        </div>
      </div>
    </div>
  );
}
