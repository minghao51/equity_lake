import type { DatasetEntry } from "../data/catalog";
import { LAYER_COLORS } from "../data/catalog";

interface DatasetTableProps {
  datasets: DatasetEntry[];
  layer?: string;
}

export default function DatasetTable({ datasets, layer }: DatasetTableProps) {
  const filtered = layer
    ? datasets.filter((d) => d.layer === layer)
    : datasets;

  const color = layer ? LAYER_COLORS[layer] || LAYER_COLORS.other : undefined;

  return (
    <div style={{ fontFamily: "monospace", padding: "16px" }}>
      <h2 style={{ color: color || "#fff", borderBottom: color ? `2px solid ${color}` : undefined, paddingBottom: "8px" }}>
        {layer
          ? `${layer.charAt(0).toUpperCase() + layer.slice(1)} Layer`
          : "All Layers"}
        <span style={{ fontSize: "14px", color: "#888", marginLeft: "12px" }}>
          {filtered.length} dataset{filtered.length !== 1 ? "s" : ""}
        </span>
      </h2>

      {filtered.map((ds) => (
        <div
          key={ds.name}
          style={{
            background: "#1a1a2e",
            border: "1px solid #333",
            borderRadius: "8px",
            padding: "16px",
            marginBottom: "16px",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: "12px", marginBottom: "12px" }}>
            <span
              style={{
                background: LAYER_COLORS[ds.layer] || "#888",
                color: "#000",
                padding: "2px 8px",
                borderRadius: "4px",
                fontSize: "12px",
                fontWeight: "bold",
              }}
            >
              {ds.layer}
            </span>
            <h3 style={{ margin: 0, color: "#e0e0e0" }}>{ds.name}</h3>
            <span
              style={{
                fontSize: "11px",
                color: "#666",
                background: "#0f0f23",
                padding: "2px 6px",
                borderRadius: "4px",
              }}
            >
              {ds.format}
            </span>
            <span
              style={{
                fontSize: "11px",
                color: "#666",
                background: "#0f0f23",
                padding: "2px 6px",
                borderRadius: "4px",
              }}
            >
              {ds.partition}
            </span>
          </div>

          <p style={{ color: "#aaa", fontSize: "13px", margin: "0 0 12px 0", lineHeight: "1.5" }}>
            {ds.description}
          </p>

          <div style={{ fontSize: "12px", color: "#888", marginBottom: "8px" }}>
            <strong>Path:</strong>{" "}
            <code style={{ background: "#0f0f23", padding: "2px 6px", borderRadius: "4px" }}>
              {ds.path}
            </code>
          </div>

          {ds.columns.length > 0 && (
            <div>
              <div style={{ fontSize: "12px", color: "#888", marginBottom: "4px" }}>
                <strong>Columns</strong> ({ds.columns.length})
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: "4px" }}>
                {ds.columns.map((col) => (
                  <span
                    key={col.name}
                    title={`${col.dtype}${col.description ? ` — ${col.description}` : ""}`}
                    style={{
                      background: "#0f0f23",
                      color: col.dtype !== "unknown" ? "#4fc3f7" : "#888",
                      padding: "2px 8px",
                      borderRadius: "4px",
                      fontSize: "11px",
                      border: "1px solid #333",
                    }}
                  >
                    {col.name}
                    <span style={{ color: "#555", marginLeft: "4px" }}>{col.dtype}</span>
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
