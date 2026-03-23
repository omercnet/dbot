import { useCallback, useEffect, useState } from "react";

type ConfiguredProvider = { has_key: boolean; base_url: string; description: string };
type ProviderSpec = {
  needs_base_url: boolean;
  needs_api_key: boolean;
  api_key_label: string;
  base_url_label: string;
  base_url_placeholder: string;
  extra_fields: { label: string; placeholder: string; required: boolean }[];
  description: string;
  configured: boolean;
};
type Toast = { message: string; ok: boolean } | null;
type JsonSchema = {
  title?: string;
  description?: string;
  type?: string;
  properties?: Record<string, JsonSchema & { default?: unknown }>;
  items?: JsonSchema;
  enum?: string[];
  anyOf?: JsonSchema[];
};

function useProviders() {
  const [configured, setConfigured] = useState<Record<string, ConfiguredProvider>>({});
  const [available, setAvailable] = useState<Record<string, ProviderSpec>>({});
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    const [cRes, aRes] = await Promise.all([
      fetch("/api/settings/providers"),
      fetch("/api/settings/providers/available"),
    ]);
    if (cRes.ok) setConfigured(await cRes.json());
    if (aRes.ok) setAvailable(await aRes.json());
    setLoading(false);
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { configured, available, loading, refresh };
}

function useSchemas() {
  const [schemas, setSchemas] = useState<Record<string, JsonSchema>>({});
  const [values, setValues] = useState<Record<string, Record<string, unknown>>>({});
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([fetch("/api/settings/schema"), fetch("/api/settings")]).then(
      async ([sRes, vRes]) => {
        if (sRes.ok) setSchemas(await sRes.json());
        if (vRes.ok) setValues(await vRes.json());
        setLoading(false);
      },
    );
  }, []);

  return { schemas, values, setValues, loading };
}

function ProviderForm({
  name,
  spec,
  existing,
  onSave,
  onDelete,
  onCancel,
}: {
  name: string;
  spec: ProviderSpec;
  existing?: ConfiguredProvider;
  onSave: (name: string, data: Record<string, string>) => Promise<boolean>;
  onDelete?: (name: string) => Promise<void>;
  onCancel?: () => void;
}) {
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState(existing?.base_url ?? "");
  const [extraValues, setExtraValues] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);

  async function handleSave() {
    setSaving(true);
    const payload: Record<string, string> = {};
    if (apiKey) payload.api_key = apiKey;
    if (spec.needs_base_url || baseUrl) payload.base_url = baseUrl;
    for (const [k, v] of Object.entries(extraValues)) {
      if (v) payload[k] = v;
    }
    const ok = await onSave(name, payload);
    setSaving(false);
    if (ok && onCancel) onCancel();
  }

  return (
    <div className="provider-row editing">
      <div className="provider-info">
        <span className="provider-name">{name}</span>
        <span className="provider-env">{spec.description}</span>
      </div>
      <div className="provider-form">
        {spec.needs_api_key && (
          <label className="schema-field">
            <span>{spec.api_key_label}</span>
            <input
              type="password"
              placeholder={
                existing?.has_key
                  ? "\u2022\u2022\u2022\u2022\u2022\u2022\u2022 (leave blank to keep)"
                  : spec.api_key_label
              }
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              className="input"
            />
          </label>
        )}
        {(spec.needs_base_url || existing?.base_url) && (
          <label className="schema-field">
            <span>{spec.base_url_label}</span>
            <input
              type="text"
              placeholder={spec.base_url_placeholder}
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              className="input"
            />
          </label>
        )}
        {spec.extra_fields.map((field) => {
          const key = field.label.toLowerCase().replace(/ /g, "_");
          return (
            <label key={key} className="schema-field">
              <span>
                {field.label}
                {field.required ? " *" : ""}
              </span>
              <input
                type="text"
                placeholder={field.placeholder}
                value={extraValues[key] ?? ""}
                onChange={(e) => setExtraValues((prev) => ({ ...prev, [key]: e.target.value }))}
                className="input"
              />
            </label>
          );
        })}
        <div className="provider-actions">
          <button type="button" className="btn btn-primary" onClick={handleSave} disabled={saving}>
            {saving ? "Saving…" : "Save"}
          </button>
          {onCancel && (
            <button type="button" className="btn btn-sm" onClick={onCancel}>
              Cancel
            </button>
          )}
          {onDelete && existing?.has_key && (
            <button type="button" className="btn btn-sm btn-danger" onClick={() => onDelete(name)}>
              Remove
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function ConfiguredProviderRow({
  name,
  provider,
  spec,
  onSave,
  onDelete,
}: {
  name: string;
  provider: ConfiguredProvider;
  spec?: ProviderSpec;
  onSave: (name: string, data: Record<string, string>) => Promise<boolean>;
  onDelete: (name: string) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);

  if (editing && spec) {
    return (
      <ProviderForm
        name={name}
        spec={spec}
        existing={provider}
        onSave={onSave}
        onDelete={onDelete}
        onCancel={() => setEditing(false)}
      />
    );
  }

  return (
    <div className="provider-row">
      <div className="provider-info">
        <span className="provider-name">{name}</span>
        <span className="provider-status configured">configured</span>
        {provider.base_url && <span className="provider-url">{provider.base_url}</span>}
      </div>
      <div className="provider-actions">
        <button type="button" className="btn btn-sm" onClick={() => setEditing(true)}>
          Edit
        </button>
        <button type="button" className="btn btn-sm btn-danger" onClick={() => onDelete(name)}>
          Remove
        </button>
      </div>
    </div>
  );
}

function fieldType(prop: JsonSchema): string {
  if (prop.type) return prop.type;
  if (prop.anyOf) {
    const types = prop.anyOf.map((a) => a.type).filter(Boolean);
    return types[0] ?? "string";
  }
  return "string";
}

function SchemaField({
  name,
  prop,
  value,
  onChange,
}: {
  name: string;
  prop: JsonSchema & { default?: unknown };
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  const type = fieldType(prop);
  const fieldLabel = prop.title ?? name.replace(/_/g, " ");

  if (type === "boolean") {
    return (
      <label className="schema-field">
        <input type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} />
        <span>{fieldLabel}</span>
      </label>
    );
  }

  if (type === "integer" || type === "number") {
    return (
      <label className="schema-field">
        <span>{fieldLabel}</span>
        <input
          type="number"
          className="input"
          step={type === "number" ? "0.1" : "1"}
          value={value as number}
          onChange={(e) =>
            onChange(type === "integer" ? parseInt(e.target.value, 10) : parseFloat(e.target.value))
          }
        />
      </label>
    );
  }

  if (type === "array") {
    const arr = Array.isArray(value) ? value : [];
    return (
      <label className="schema-field">
        <span>{fieldLabel}</span>
        <textarea
          className="input"
          rows={3}
          value={arr.join("\n")}
          onChange={(e) =>
            onChange(
              e.target.value
                .split("\n")
                .map((s) => s.trim())
                .filter(Boolean),
            )
          }
          placeholder="One item per line"
        />
      </label>
    );
  }

  if (type === "object") {
    const obj =
      typeof value === "object" && value !== null ? (value as Record<string, unknown>) : {};
    return (
      <label className="schema-field">
        <span>{fieldLabel}</span>
        <textarea
          className="input"
          rows={4}
          value={JSON.stringify(obj, null, 2)}
          onChange={(e) => {
            try {
              onChange(JSON.parse(e.target.value));
            } catch {
              /* let them keep typing */
            }
          }}
        />
      </label>
    );
  }

  if (prop.enum) {
    return (
      <label className="schema-field">
        <span>{fieldLabel}</span>
        <select
          className="input"
          value={String(value ?? "")}
          onChange={(e) => onChange(e.target.value)}
        >
          {prop.enum.map((opt) => (
            <option key={opt} value={opt}>
              {opt}
            </option>
          ))}
        </select>
      </label>
    );
  }

  return (
    <label className="schema-field">
      <span>{fieldLabel}</span>
      <input
        type={name.includes("key") || name.includes("secret") ? "password" : "text"}
        className="input"
        value={String(value ?? "")}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  );
}

function SchemaSection({
  name,
  schema,
  values,
  onSave,
}: {
  name: string;
  schema: JsonSchema;
  values: Record<string, unknown>;
  onSave: (section: string, data: Record<string, unknown>) => Promise<void>;
}) {
  const [data, setData] = useState(values);
  const [saving, setSaving] = useState(false);
  const props = schema.properties ?? {};

  useEffect(() => {
    setData(values);
  }, [values]);

  function updateField(field: string, val: unknown) {
    setData((prev) => ({ ...prev, [field]: val }));
  }

  async function handleSave() {
    setSaving(true);
    await onSave(name, data);
    setSaving(false);
  }

  const skipFields = new Set(["providers"]);

  return (
    <section className="settings-section">
      <h2>{schema.title ?? name}</h2>
      {schema.description && <p className="settings-hint">{schema.description}</p>}
      <div className="schema-fields">
        {Object.entries(props)
          .filter(([key]) => !skipFields.has(key))
          .map(([key, prop]) => (
            <SchemaField
              key={key}
              name={key}
              prop={prop}
              value={data[key]}
              onChange={(v) => updateField(key, v)}
            />
          ))}
      </div>
      <div className="settings-actions">
        <button type="button" className="btn btn-primary" onClick={handleSave} disabled={saving}>
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
    </section>
  );
}

type Tab = "providers" | string;

export function SettingsPage({ onBack }: { onBack: () => void }) {
  const { configured, available, loading: providersLoading, refresh } = useProviders();
  const { schemas, values, setValues, loading: schemasLoading } = useSchemas();
  const [toast, setToast] = useState<Toast>(null);
  const [reloading, setReloading] = useState(false);
  const [activeTab, setActiveTab] = useState<Tab>("providers");
  const [addingProvider, setAddingProvider] = useState<string | null>(null);

  function showToast(message: string, ok: boolean) {
    setToast({ message, ok });
    setTimeout(() => setToast(null), 3000);
  }

  async function saveProvider(name: string, data: { api_key?: string; base_url?: string }) {
    const r = await fetch(`/api/settings/providers/${name}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    if (r.ok) {
      showToast(`${name} saved`, true);
      setAddingProvider(null);
      await refresh();
      return true;
    }
    showToast(`Failed to save ${name}`, false);
    return false;
  }

  async function deleteProvider(name: string) {
    const r = await fetch(`/api/settings/providers/${name}`, { method: "DELETE" });
    if (r.ok) {
      showToast(`${name} removed`, true);
      await refresh();
    } else {
      showToast(`Failed to remove ${name}`, false);
    }
  }

  async function saveSection(section: string, data: Record<string, unknown>) {
    const r = await fetch(`/api/settings/${section}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    if (r.ok) {
      showToast(`${section} saved`, true);
      setValues((prev) => ({ ...prev, [section]: data }));
    } else {
      const body = await r.json().catch(() => ({}));
      showToast(`Failed: ${body.detail || "validation error"}`, false);
    }
  }

  async function reloadApp() {
    setReloading(true);
    const r = await fetch("/api/reload", { method: "POST" });
    setReloading(false);
    if (r.ok) {
      showToast("App reloaded", true);
    } else {
      const body = await r.json().catch(() => ({}));
      showToast(`Reload failed: ${body.detail || "unknown error"}`, false);
    }
  }

  const configuredCount = Object.keys(configured).length;
  const unconfigured = Object.entries(available).filter(([name]) => !configured[name]);

  const tabs: { id: Tab; label: string }[] = [
    { id: "providers", label: `Providers (${configuredCount})` },
    ...Object.entries(schemas).map(([id, s]) => ({ id, label: s.title ?? id })),
  ];

  return (
    <div className="chat-layout">
      <header className="chat-header">
        <button type="button" className="btn btn-sm" onClick={onBack}>
          ← Chat
        </button>
        <span className="logo">Settings</span>
        <button
          type="button"
          className="btn btn-sm btn-primary"
          style={{ marginLeft: "auto" }}
          onClick={reloadApp}
          disabled={reloading}
        >
          {reloading ? "Reloading…" : "Reload App"}
        </button>
      </header>

      <div className="settings-content">
        <nav className="settings-tabs">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              type="button"
              className={`tab-btn ${activeTab === tab.id ? "active" : ""}`}
              onClick={() => setActiveTab(tab.id)}
            >
              {tab.label}
            </button>
          ))}
        </nav>

        {providersLoading || schemasLoading ? (
          <div className="settings-loading">Loading…</div>
        ) : activeTab === "providers" ? (
          <section className="settings-section">
            <p className="settings-hint">
              Configure at least one provider to enable chat. After saving, click Reload to apply.
            </p>

            {configuredCount > 0 && (
              <div className="provider-list">
                {Object.entries(configured).map(([name, provider]) => (
                  <ConfiguredProviderRow
                    key={name}
                    name={name}
                    provider={provider}
                    spec={available[name]}
                    onSave={saveProvider}
                    onDelete={deleteProvider}
                  />
                ))}
              </div>
            )}

            {addingProvider && available[addingProvider] ? (
              <div className="provider-list" style={{ marginTop: 12 }}>
                <ProviderForm
                  name={addingProvider}
                  spec={available[addingProvider]}
                  onSave={saveProvider}
                  onCancel={() => setAddingProvider(null)}
                />
              </div>
            ) : unconfigured.length > 0 ? (
              <div className="settings-actions">
                <select
                  className="input"
                  value=""
                  onChange={(e) => setAddingProvider(e.target.value)}
                >
                  <option value="" disabled>
                    + Add provider…
                  </option>
                  {unconfigured.map(([name, spec]) => (
                    <option key={name} value={name}>
                      {name} — {spec.description}
                    </option>
                  ))}
                </select>
              </div>
            ) : null}

            {configuredCount === 0 && !addingProvider && (
              <div className="empty-state" style={{ padding: "40px 0" }}>
                <p>No providers configured. Add one to get started.</p>
              </div>
            )}
          </section>
        ) : schemas[activeTab] ? (
          <SchemaSection
            name={activeTab}
            schema={schemas[activeTab]}
            values={(values[activeTab] as Record<string, unknown>) ?? {}}
            onSave={saveSection}
          />
        ) : null}
      </div>

      {toast && (
        <div className={`toast ${toast.ok ? "toast-ok" : "toast-err"}`}>{toast.message}</div>
      )}
    </div>
  );
}
