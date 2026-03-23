import { useState } from "react";

type CredentialRequired = {
  pack: string;
  required_credentials: string[];
};

export function CredentialDialog({
  cred,
  onSave,
  onDismiss,
}: {
  cred: CredentialRequired;
  onSave: (pack: string, credentials: Record<string, string>) => Promise<boolean>;
  onDismiss: () => void;
}) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);

  async function handleSave() {
    setSaving(true);
    const ok = await onSave(cred.pack, values);
    setSaving(false);
    if (!ok) return;
  }

  return (
    <div className="dialog-overlay" data-testid="credential-dialog">
      <div className="dialog">
        <div className="dialog-header">
          <h3>Credentials Required</h3>
          <span className="dialog-subtitle">
            Pack <strong>{cred.pack}</strong> needs credentials to continue.
          </span>
        </div>

        <div className="dialog-body">
          {cred.required_credentials.map((name) => (
            <label key={name} className="schema-field">
              <span>{name}</span>
              <input
                type="password"
                className="input"
                placeholder={name}
                value={values[name] ?? ""}
                onChange={(e) => setValues((prev) => ({ ...prev, [name]: e.target.value }))}
              />
            </label>
          ))}
        </div>

        <div className="dialog-footer">
          <button type="button" className="btn btn-primary" onClick={handleSave} disabled={saving}>
            {saving ? "Saving\u2026" : "Save & Retry"}
          </button>
          <button type="button" className="btn btn-sm" onClick={onDismiss}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}

export function detectCredentialRequired(
  messages: { parts: { type: string; output?: unknown }[] }[],
): CredentialRequired | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    const msg = messages[i];
    for (const part of msg.parts) {
      if (
        part.type === "dynamic-tool" ||
        (typeof part.type === "string" && part.type.startsWith("tool-"))
      ) {
        const output = (part as { output?: Record<string, unknown> }).output;
        if (output && output.status === "credentials_required" && typeof output.pack === "string") {
          return {
            pack: output.pack as string,
            required_credentials: Array.isArray(output.required_credentials)
              ? (output.required_credentials as string[])
              : [],
          };
        }
      }
    }
  }
  return null;
}
