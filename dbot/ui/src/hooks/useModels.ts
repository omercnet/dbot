import { useEffect, useState } from "react";

export type ModelInfo = {
  id: string;
  name: string;
};

export function useModels() {
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [selected, setSelected] = useState<string>("");

  useEffect(() => {
    fetch("/api/settings/models")
      .then((r) => (r.ok ? r.json() : {}))
      .then((data: Record<string, string>) => {
        const list = Object.entries(data).map(([name, id]) => ({ name, id }));
        setModels(list);
        if (list.length > 0 && !selected) {
          setSelected(list[0].id);
        }
      });
  }, []); // biome-ignore lint/correctness/useExhaustiveDependencies: only fetch on mount

  return { models, selected, setSelected };
}
