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
    try {
      const [cRes, aRes] = await Promise.all([
        fetch("/api/settings/providers"),
        fetch("/api/settings/providers/available"),
      ]);
      if (cRes.ok) setConfigured(await cRes.json());
      if (aRes.ok) setAvailable(await aRes.json());
    } catch {
      // Network error — leave state as-is
    }
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
    Promise.all([fetch("/api/settings/schema"), fetch("/api/settings")])
      .then(async ([sRes, vRes]) => {
        if (sRes.ok) setSchemas(await sRes.json());
        if (vRes.ok) setValues(await vRes.json());
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  return { schemas, values, setValues, loading };
}

function useConfiguredModels() {
  const [models, setModels] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch("/api/settings/models");
      if (r.ok) setModels(await r.json());
    } catch {
      // Network error
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { models, loading, refresh };
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
          value={typeof value === "number" ? value : ""}
          onChange={(e) => {
            const v = e.target.value;
            if (v === "") {
              onChange(undefined);
              return;
            }
            const n = type === "integer" ? parseInt(v, 10) : parseFloat(v);
            if (!Number.isNaN(n)) onChange(n);
          }}
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

function ModelsTab({
  models,
  providers,
  onAdd,
  onDelete,
}: {
  models: Record<string, string>;
  providers: string[];
  onAdd: (name: string, provider: string, model: string) => Promise<void>;
  onDelete: (name: string) => Promise<void>;
}) {
  const [adding, setAdding] = useState(false);
  const [displayName, setDisplayName] = useState("");
  const [provider, setProvider] = useState(providers[0] ?? "");
  const [modelName, setModelName] = useState("");
  const [saving, setSaving] = useState(false);

  async function handleAdd() {
    if (!displayName.trim() || !provider || !modelName.trim()) return;
    setSaving(true);
    await onAdd(displayName.trim(), provider, modelName.trim());
    setSaving(false);
    setDisplayName("");
    setModelName("");
    setAdding(false);
  }

  const entries = Object.entries(models);

  return (
    <section className="settings-section">
      <p className="settings-hint">
        Add models as provider:model combos. The display name appears in the chat dropdown.
      </p>

      {entries.length > 0 && (
        <div className="provider-list">
          {entries.map(([name, modelId]) => (
            <div key={name} className="provider-row">
              <div className="provider-info">
                <span className="provider-name">{name}</span>
                <span className="provider-url">{modelId}</span>
              </div>
              <div className="provider-actions">
                <button
                  type="button"
                  className="btn btn-sm btn-danger"
                  onClick={() => onDelete(name)}
                >
                  Remove
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {adding ? (
        <div className="provider-row editing" style={{ marginTop: 12 }}>
          <div className="provider-form">
            <label className="schema-field">
              <span>Display Name</span>
              <input
                type="text"
                className="input"
                placeholder="e.g. GPT-4o (Azure)"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
              />
            </label>
            <label className="schema-field">
              <span>Provider</span>
              <select
                className="input"
                value={provider}
                onChange={(e) => setProvider(e.target.value)}
              >
                {providers.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </label>
            <label className="schema-field">
              <span>Model Name</span>
              <input
                type="text"
                className="input"
                placeholder="e.g. gpt-4o, claude-sonnet-4-5, my-deployment"
                value={modelName}
                onChange={(e) => setModelName(e.target.value)}
              />
            </label>
            <div className="provider-actions">
              <button
                type="button"
                className="btn btn-primary"
                onClick={handleAdd}
                disabled={saving}
              >
                {saving ? "Adding\u2026" : "Add Model"}
              </button>
              <button type="button" className="btn btn-sm" onClick={() => setAdding(false)}>
                Cancel
              </button>
            </div>
          </div>
        </div>
      ) : (
        <div className="settings-actions">
          <button type="button" className="btn btn-primary" onClick={() => setAdding(true)}>
            + Add Model
          </button>
        </div>
      )}

      {entries.length === 0 && !adding && (
        <div className="empty-state" style={{ padding: "40px 0" }}>
          <p>No models configured. Add one to start chatting.</p>
        </div>
      )}
    </section>
  );
}

function usePackCredentials() {
  const [packs, setPacks] = useState<Record<string, string[]>>({});
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    const r = await fetch("/api/settings/credentials");
    if (r.ok) setPacks(await r.json());
    setLoading(false);
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { packs, loading, refresh };
}

type PackParam = {
  name: string;
  display: string;
  display_password: string;
  type: number;
  required: boolean;
  default: string | null;
};

function CredPackEditForm({
  pack,
  onSave,
  onCancel,
}: {
  pack: string;
  onSave: (pack: string, values: Record<string, string>) => Promise<void>;
  onCancel: () => void;
}) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [params, setParams] = useState<PackParam[]>([]);
  const [loadingParams, setLoadingParams] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    fetch(`/api/packs/${encodeURIComponent(pack)}/params`)
      .then((r) => (r.ok ? r.json() : { params: [] }))
      .then((data: { params: PackParam[] }) => {
        const visible = data.params.filter(
          (p) => ![8, 15, 17].includes(p.type) && (p.required || p.type === 4 || p.type === 9),
        );
        setParams(visible);
        const defaults: Record<string, string> = {};
        for (const p of visible) {
          if (p.default) defaults[p.name] = p.default;
        }
        setValues(defaults);
      })
      .finally(() => setLoadingParams(false));
  }, [pack]);

  async function handleSave() {
    setSaving(true);
    await onSave(pack, values);
    setSaving(false);
    onCancel();
  }

  function renderParam(p: PackParam) {
    if (p.type === 9) {
      return (
        <div key={p.name}>
          <label className="schema-field">
            <span>{p.display || "Username"}</span>
            <input
              type="text"
              className="input"
              placeholder="(leave blank to keep)"
              value={values[`${p.name}_id`] ?? ""}
              onChange={(e) => setValues((prev) => ({ ...prev, [`${p.name}_id`]: e.target.value }))}
            />
          </label>
          <label className="schema-field">
            <span>{p.display_password || "Password"}</span>
            <input
              type="password"
              className="input"
              placeholder="(leave blank to keep)"
              value={values[`${p.name}_password`] ?? ""}
              onChange={(e) =>
                setValues((prev) => ({ ...prev, [`${p.name}_password`]: e.target.value }))
              }
            />
          </label>
        </div>
      );
    }
    const isSecret = p.type === 4;
    return (
      <label key={p.name} className="schema-field">
        <span>{p.display || p.name}</span>
        <input
          type={isSecret ? "password" : "text"}
          className="input"
          placeholder={p.default || "(leave blank to keep)"}
          value={values[p.name] ?? ""}
          onChange={(e) => setValues((prev) => ({ ...prev, [p.name]: e.target.value }))}
        />
      </label>
    );
  }

  return (
    <div className="provider-row editing">
      <div className="provider-info">
        <span className="provider-name">{pack}</span>
      </div>
      <div className="provider-form">
        {loadingParams ? (
          <span style={{ color: "var(--text-muted)", fontSize: 13 }}>Loading params...</span>
        ) : (
          params.map(renderParam)
        )}
        <div className="provider-actions">
          <button
            type="button"
            className="btn btn-primary"
            onClick={handleSave}
            disabled={saving || loadingParams}
          >
            {saving ? "Saving\u2026" : "Save"}
          </button>
          <button type="button" className="btn btn-sm" onClick={onCancel}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}

function CredentialsTab({
  packs,
  onDelete,
  onTest,
  onSave,
}: {
  packs: Record<string, string[]>;
  onDelete: (pack: string) => Promise<void>;
  onTest: (pack: string) => Promise<boolean>;
  onSave: (pack: string, values: Record<string, string>) => Promise<void>;
}) {
  const [editing, setEditing] = useState<string | null>(null);
  const entries = Object.entries(packs);

  return (
    <section className="settings-section">
      <p className="settings-hint">
        Integration credentials configured via the chat credential dialog or API. These are used
        when invoking security tools.
      </p>

      {entries.length === 0 ? (
        <div className="empty-state" style={{ padding: "40px 0" }}>
          <p>
            No integration credentials configured yet. They will appear here when you configure them
            during chat.
          </p>
        </div>
      ) : (
        <div className="provider-list">
          {entries.map(([pack, params]) =>
            editing === pack ? (
              <CredPackEditForm
                key={pack}
                pack={pack}
                onSave={onSave}
                onCancel={() => setEditing(null)}
              />
            ) : (
              <div key={pack} className="provider-row">
                <div className="provider-info">
                  <span className="provider-name">{pack}</span>
                  <span className="provider-url">{params.join(", ")}</span>
                </div>
                <div className="provider-actions">
                  <button type="button" className="btn btn-sm" onClick={() => setEditing(pack)}>
                    Edit
                  </button>
                  <button type="button" className="btn btn-sm" onClick={() => onTest(pack)}>
                    Test
                  </button>
                  <button
                    type="button"
                    className="btn btn-sm btn-danger"
                    onClick={() => onDelete(pack)}
                  >
                    Remove
                  </button>
                </div>
              </div>
            ),
          )}
        </div>
      )}
    </section>
  );
}

type Tab = "providers" | "models" | "credentials" | string;

export function SettingsPage({ onBack }: { onBack: () => void }) {
  const { configured, available, loading: providersLoading, refresh } = useProviders();
  const { models, loading: modelsLoading, refresh: refreshModels } = useConfiguredModels();
  const { packs: credPacks, loading: credsLoading, refresh: refreshCreds } = usePackCredentials();
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
  const modelCount = Object.keys(models).length;
  const unconfigured = Object.entries(available).filter(([name]) => !configured[name]);
  const providerNames = Object.keys(available);

  async function addModel(displayName: string, provider: string, model: string) {
    const r = await fetch("/api/settings/models", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: displayName, provider, model }),
    });
    if (r.ok) {
      showToast(`${displayName} added`, true);
      await refreshModels();
    } else {
      showToast("Failed to add model", false);
    }
  }

  async function deleteModel(displayName: string) {
    const r = await fetch(`/api/settings/models/${encodeURIComponent(displayName)}`, {
      method: "DELETE",
    });
    if (r.ok) {
      showToast(`${displayName} removed`, true);
      await refreshModels();
    } else {
      showToast("Failed to remove model", false);
    }
  }

  const credCount = Object.keys(credPacks).length;

  async function deleteCredPack(pack: string) {
    const r = await fetch(`/api/settings/credentials/${encodeURIComponent(pack)}`, {
      method: "DELETE",
    });
    if (r.ok) {
      showToast(`${pack} credentials removed`, true);
      await refreshCreds();
    } else {
      showToast(`Failed to remove ${pack}`, false);
    }
  }

  async function testCredPack(pack: string): Promise<boolean> {
    const r = await fetch(`/api/settings/credentials/${encodeURIComponent(pack)}/test`, {
      method: "POST",
    });
    const body = await r.json().catch(() => ({}));
    if (r.ok && body.success) {
      showToast(`${pack}: connection OK`, true);
      return true;
    }
    showToast(`${pack}: ${body.error || "test failed"}`, false);
    return false;
  }

  const tabs: { id: Tab; label: string }[] = [
    { id: "providers", label: `Providers (${configuredCount})` },
    { id: "models", label: `Models (${modelCount})` },
    { id: "credentials", label: `Integrations (${credCount})` },
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

        {providersLoading || modelsLoading || schemasLoading || credsLoading ? (
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
        ) : activeTab === "models" ? (
          <ModelsTab
            models={models}
            providers={providerNames}
            onAdd={addModel}
            onDelete={deleteModel}
          />
        ) : activeTab === "credentials" ? (
          <CredentialsTab
            packs={credPacks}
            onDelete={deleteCredPack}
            onTest={testCredPack}
            onSave={async (pack, values) => {
              const entries = Object.entries(values).filter(([, v]) => v);
              if (entries.length === 0) return;
              for (const [name, value] of entries) {
                await fetch(`/api/settings/credentials/${encodeURIComponent(pack)}`, {
                  method: "PUT",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ [name]: value }),
                });
              }
              showToast(`${pack} credentials updated`, true);
              await refreshCreds();
            }}
          />
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
