import { useState } from "react";

type ConfigParam = {
  name: string;
  display: string;
  display_password?: string;
  type: number;
  required: boolean;
  default?: string;
};

type CredentialRequired = {
  pack: string;
  required_credentials: string[];
  config_params?: ConfigParam[];
};

function ParamField({
  param,
  values,
  onChange,
}: {
  param: ConfigParam;
  values: Record<string, string>;
  onChange: (key: string, value: string) => void;
}) {
  if (param.type === 9) {
    return (
      <>
        <label className="schema-field">
          <span>{param.display || "Username"}</span>
          <input
            type="text"
            className="input"
            placeholder={param.display || "Username"}
            value={values[`${param.name}_id`] ?? ""}
            onChange={(e) => onChange(`${param.name}_id`, e.target.value)}
          />
        </label>
        <label className="schema-field">
          <span>{param.display_password || "Password"}</span>
          <input
            type="password"
            className="input"
            placeholder={param.display_password || "Password"}
            value={values[`${param.name}_password`] ?? ""}
            onChange={(e) => onChange(`${param.name}_password`, e.target.value)}
          />
        </label>
      </>
    );
  }

  const isSecret = param.type === 4;
  return (
    <label className="schema-field">
      <span>{param.display || param.name}</span>
      <input
        type={isSecret ? "password" : "text"}
        className="input"
        placeholder={param.default || param.display || param.name}
        value={values[param.name] ?? param.default ?? ""}
        onChange={(e) => onChange(param.name, e.target.value)}
      />
    </label>
  );
}

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

  const handleChange = (key: string, value: string) => {
    setValues((prev) => ({ ...prev, [key]: value }));
  };

  async function handleSave() {
    setSaving(true);
    const ok = await onSave(cred.pack, values);
    setSaving(false);
    if (!ok) return;
  }

  const hasRichParams = cred.config_params && cred.config_params.length > 0;

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
          {hasRichParams
            ? cred.config_params!.map((param) => (
                <ParamField
                  key={param.name}
                  param={param}
                  values={values}
                  onChange={handleChange}
                />
              ))
            : cred.required_credentials.map((name) => (
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
            config_params: Array.isArray(output.config_params)
              ? (output.config_params as ConfigParam[])
              : undefined,
          };
        }
      }
    }
  }
  return null;
}
