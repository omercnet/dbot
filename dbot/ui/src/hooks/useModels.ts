import { useEffect, useState } from "react";

export type ModelInfo = {
  id: string;
  name: string;
};

export function useModels() {
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [selected, setSelected] = useState<string>("");

  useEffect(() => {
    fetch("/api/configure")
      .then((r) => (r.ok ? r.json() : { models: [] }))
      .then((data: { models: ModelInfo[] }) => {
        setModels(data.models);
        if (data.models.length > 0 && !selected) {
          setSelected(data.models[0].id);
        }
      });
  }, []); // biome-ignore lint/correctness/useExhaustiveDependencies: only fetch on mount

  return { models, selected, setSelected };
}
