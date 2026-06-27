import type { Catalog } from "../data/catalog";
import { LAYER_COLORS, LAYER_LABELS } from "../data/catalog";

interface LayerStatsProps {
  catalog: Catalog;
}

export default function LayerStats({ catalog }: LayerStatsProps) {
  const layers = ["bronze", "silver", "gold", "platinum"];
  const layerOrder = layers.filter((l) => catalog.datasets.some((d) => d.layer === l));

  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: "16px", padding: "16px" }}>
      {layerOrder.map((layer) => {
        const datasets = catalog.datasets.filter((d) => d.layer === layer);
        const nodes = catalog.nodes.filter((n) => n.layer === layer);
        const color = LAYER_COLORS[layer] || "#888";

        return (
          <a
            key={layer}
            href={`/${layer}`}
            style={{
              textDecoration: "none",
              color: "inherit",
              display: "block",
              background: "#1a1a2e",
              border: `2px solid ${color}44`,
              borderRadius: "12px",
              padding: "20px",
              transition: "border-color 0.2s",
            }}
          >
            <h3 style={{ color, margin: "0 0 12px 0", fontSize: "18px" }}>
              {LAYER_LABELS[layer] || layer}
            </h3>
            <div style={{ display: "flex", gap: "24px" }}>
              <div>
                <div style={{ fontSize: "24px", fontWeight: "bold", color: "#e0e0e0" }}>
                  {datasets.length}
                </div>
                <div style={{ fontSize: "12px", color: "#888" }}>datasets</div>
              </div>
              <div>
                <div style={{ fontSize: "24px", fontWeight: "bold", color: "#e0e0e0" }}>
                  {nodes.length}
                </div>
                <div style={{ fontSize: "12px", color: "#888" }}>DAG nodes</div>
              </div>
              <div>
                <div style={{ fontSize: "24px", fontWeight: "bold", color: "#e0e0e0" }}>
                  {datasets.reduce((sum, ds) => sum + ds.columns.length, 0)}
                </div>
                <div style={{ fontSize: "12px", color: "#888" }}>columns</div>
              </div>
            </div>
          </a>
        );
      })}
    </div>
  );
}
