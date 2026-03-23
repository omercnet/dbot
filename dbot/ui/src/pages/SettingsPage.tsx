import { useCallback, useEffect, useState } from "react";

type Provider = { has_key: boolean; env_var: string; base_url: string };
type ProviderMap = Record<string, Provider>;
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
type SectionSchemas = Record<string, JsonSchema>;

function useProviders() {
  const [providers, setProviders] = useState<ProviderMap>({});
  const [loading, setLoading] = useState(true);
  const refresh = useCallback(async () => {
    setLoading(true);
    const r = await fetch("/api/settings/providers");
    if (r.ok) setProviders(await r.json());
    setLoading(false);
  }, []);
  useEffect(() => {
    refresh();
  }, [refresh]);
  return { providers, loading, refresh };
}

function useSchemas() {
  const [schemas, setSchemas] = useState<SectionSchemas>({});
  const [values, setValues] = useState<Record<string, Record<string, unknown>>>({});
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([fetch("/api/settings/schema"), fetch("/api/settings")]).then(
      async ([schemaRes, valuesRes]) => {
        if (schemaRes.ok) setSchemas(await schemaRes.json());
        if (valuesRes.ok) setValues(await valuesRes.json());
        setLoading(false);
      },
    );
  }, []);

  return { schemas, values, setValues, loading };
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
  const label = prop.title ?? name.replace(/_/g, " ");

  if (type === "boolean") {
    return (
      <label className="schema-field">
        <input type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} />
        <span>{label}</span>
      </label>
    );
  }

  if (type === "integer" || type === "number") {
    return (
      <label className="schema-field">
        <span>{label}</span>
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
        <span>{label}</span>
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
        <span>{label}</span>
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
        <span>{label}</span>
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
      <span>{label}</span>
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

function ProviderRow({
  name,
  provider,
  onSave,
  onDelete,
}: {
  name: string;
  provider: Provider;
  onSave: (name: string, data: { api_key?: string; base_url?: string }) => Promise<boolean>;
  onDelete: (name: string) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState(provider.base_url);
  const [saving, setSaving] = useState(false);

  async function handleSave() {
    setSaving(true);
    const payload: { api_key?: string; base_url?: string } = { base_url: baseUrl };
    if (apiKey) payload.api_key = apiKey;
    const ok = await onSave(name, payload);
    setSaving(false);
    if (ok) {
      setEditing(false);
      setApiKey("");
    }
  }

  if (!editing) {
    return (
      <div className="provider-row">
        <div className="provider-info">
          <span className="provider-name">{name}</span>
          <span className={`provider-status ${provider.has_key ? "configured" : ""}`}>
            {provider.has_key ? "configured" : "not set"}
          </span>
          {provider.base_url && <span className="provider-url">{provider.base_url}</span>}
        </div>
        <div className="provider-actions">
          <button type="button" className="btn btn-sm" onClick={() => setEditing(true)}>
            {provider.has_key ? "Edit" : "Configure"}
          </button>
          {provider.has_key && (
            <button type="button" className="btn btn-sm btn-danger" onClick={() => onDelete(name)}>
              Remove
            </button>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="provider-row editing">
      <div className="provider-info">
        <span className="provider-name">{name}</span>
        <span className="provider-env">{provider.env_var}</span>
      </div>
      <div className="provider-form">
        <input
          type="password"
          placeholder={provider.has_key ? "••••••• (leave blank to keep)" : "API key"}
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          className="input"
        />
        <input
          type="text"
          placeholder="Base URL (optional)"
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
          className="input"
        />
        <div className="provider-actions">
          <button type="button" className="btn btn-primary" onClick={handleSave} disabled={saving}>
            {saving ? "Saving…" : "Save"}
          </button>
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => {
              setEditing(false);
              setApiKey("");
            }}
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}

type Tab = "providers" | string;

export function SettingsPage({ onBack }: { onBack: () => void }) {
  const { providers, loading: providersLoading, refresh } = useProviders();
  const { schemas, values, setValues, loading: schemasLoading } = useSchemas();
  const [toast, setToast] = useState<Toast>(null);
  const [reloading, setReloading] = useState(false);
  const [activeTab, setActiveTab] = useState<Tab>("providers");

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

  const configured = Object.values(providers).filter((p) => p.has_key).length;
  const tabs: { id: Tab; label: string }[] = [
    { id: "providers", label: `Providers (${configured})` },
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
            <div className="provider-list">
              {Object.entries(providers).map(([name, provider]) => (
                <ProviderRow
                  key={name}
                  name={name}
                  provider={provider}
                  onSave={saveProvider}
                  onDelete={deleteProvider}
                />
              ))}
            </div>
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
