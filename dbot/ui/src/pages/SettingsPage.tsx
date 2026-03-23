import { useCallback, useEffect, useState } from "react";

type Provider = {
  has_key: boolean;
  env_var: string;
  base_url: string;
};

type ProviderMap = Record<string, Provider>;

type Toast = { message: string; ok: boolean } | null;

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
    const data: { api_key?: string; base_url?: string } = { base_url: baseUrl };
    if (apiKey) data.api_key = apiKey;
    const ok = await onSave(name, data);
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

export function SettingsPage({ onBack }: { onBack: () => void }) {
  const { providers, loading, refresh } = useProviders();
  const [toast, setToast] = useState<Toast>(null);
  const [reloading, setReloading] = useState(false);

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

  async function reloadApp() {
    setReloading(true);
    const r = await fetch("/api/reload", { method: "POST" });
    setReloading(false);
    if (r.ok) {
      showToast("App reloaded — chat enabled", true);
    } else {
      const body = await r.json().catch(() => ({}));
      showToast(`Reload failed: ${body.detail || "unknown error"}`, false);
    }
  }

  const configured = Object.values(providers).filter((p) => p.has_key).length;

  return (
    <div className="chat-layout">
      <header className="chat-header">
        <button type="button" className="btn btn-sm" onClick={onBack}>
          ← Chat
        </button>
        <span className="logo">Settings</span>
      </header>

      <div className="settings-content">
        <section className="settings-section">
          <div className="settings-section-header">
            <h2>LLM Providers</h2>
            <span className="badge">{configured} configured</span>
          </div>
          <p className="settings-hint">
            Configure at least one provider to enable chat. After saving, click Reload to apply.
          </p>

          {loading ? (
            <div className="settings-loading">Loading providers…</div>
          ) : (
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
          )}

          <div className="settings-actions">
            <button
              type="button"
              className="btn btn-primary"
              onClick={reloadApp}
              disabled={reloading || configured === 0}
            >
              {reloading ? "Reloading…" : "Reload App"}
            </button>
          </div>
        </section>
      </div>

      {toast && (
        <div className={`toast ${toast.ok ? "toast-ok" : "toast-err"}`}>{toast.message}</div>
      )}
    </div>
  );
}
