export interface ColumnInfo {
  name: string;
  dtype: string;
  nullable: boolean;
  description: string;
}

export interface DatasetEntry {
  name: string;
  layer: string;
  path: string;
  description: string;
  format: string;
  partition: string;
  columns: ColumnInfo[];
  upstream: string[];
  downstream: string[];
}

export interface NodeEntry {
  name: string;
  layer: string;
  category: string;
  description: string;
  produces: string[];
  depends_on: string[];
  validators: string[];
  tags: Record<string, unknown>;
}

export interface EdgeEntry {
  source: string;
  target: string;
  relationship: string;
}

export interface Catalog {
  version: string;
  datasets: DatasetEntry[];
  nodes: NodeEntry[];
  edges: EdgeEntry[];
}

export function parseCatalog(jsonl: string): Catalog {
  const lines = jsonl.trim().split("\n");
  const datasets: DatasetEntry[] = [];
  const nodes: NodeEntry[] = [];
  const edges: EdgeEntry[] = [];
  let version = "";

  for (const line of lines) {
    if (!line.trim()) continue;
    const obj = JSON.parse(line);
    switch (obj.type) {
      case "catalog":
        version = obj.version;
        break;
      case "dataset":
        datasets.push(obj as DatasetEntry);
        break;
      case "node":
        nodes.push(obj as NodeEntry);
        break;
      case "edge":
        edges.push(obj as EdgeEntry);
        break;
    }
  }

  return { version, datasets, nodes, edges };
}

export function getDatasetsByLayer(catalog: Catalog): Record<string, DatasetEntry[]> {
  const byLayer: Record<string, DatasetEntry[]> = {};
  for (const ds of catalog.datasets) {
    if (!byLayer[ds.layer]) byLayer[ds.layer] = [];
    byLayer[ds.layer].push(ds);
  }
  return byLayer;
}

export function getNodesByLayer(catalog: Catalog): Record<string, NodeEntry[]> {
  const byLayer: Record<string, NodeEntry[]> = {};
  for (const node of catalog.nodes) {
    const layer = node.layer || "other";
    if (!byLayer[layer]) byLayer[layer] = [];
    byLayer[layer].push(node);
  }
  return byLayer;
}

export const LAYER_COLORS: Record<string, string> = {
  bronze: "#cd7f32",
  silver: "#c0c0c0",
  gold: "#ffd700",
  platinum: "#e5e4e2",
  other: "#888888",
};

export const LAYER_LABELS: Record<string, string> = {
  bronze: "Bronze — Raw",
  silver: "Silver — Validated",
  gold: "Gold — Features",
  platinum: "Platinum — Predictions",
};
