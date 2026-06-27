import {
  ReactFlow,
  MiniMap,
  Controls,
  Background,
  BackgroundVariant,
  Handle,
  Position,
  type Node,
  type Edge,
  type NodeProps,
  type NodeTypes,
} from "@xyflow/react";
import dagre from "@dagrejs/dagre";
import "@xyflow/react/dist/style.css";
import type { EdgeEntry, NodeEntry } from "../data/catalog";
import { LAYER_COLORS } from "../data/catalog";

type CatalogNodeData = { entry: NodeEntry };

const NODE_WIDTH = 180;
const NODE_HEIGHT = 80;

const catalogNodeTypes: NodeTypes = {
  catalog: CatalogNode,
};

function CatalogNode({ data }: NodeProps<Node<CatalogNodeData>>) {
  const entry = data.entry;
  const color = LAYER_COLORS[entry.layer] || LAYER_COLORS.other;
  const layerLabel = entry.layer
    ? entry.layer.charAt(0).toUpperCase() + entry.layer.slice(1)
    : "";

  return (
    <div
      style={{
        border: `2px solid ${color}`,
        borderRadius: "6px",
        background: "#1a1a2e",
        color: "#e0e0e0",
        minWidth: "140px",
        fontSize: "12px",
      }}
    >
      <Handle type="target" position={Position.Left} />
      <div
        style={{
          background: color,
          color: "#000",
          padding: "2px 8px",
          fontSize: "10px",
          fontWeight: "bold",
          borderRadius: "4px 4px 0 0",
        }}
      >
        {layerLabel}
      </div>
      <div style={{ padding: "8px" }}>
        <div style={{ fontWeight: "bold", marginBottom: "4px" }}>
          {entry.name}
        </div>
        {entry.category && (
          <div style={{ fontSize: "10px", color: "#888" }}>{entry.category}</div>
        )}
        {entry.validators.length > 0 && (
          <div style={{ fontSize: "10px", color: "#ff6b6b", marginTop: "4px" }}>
            {entry.validators.length} validator(s)
          </div>
        )}
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

export function buildLineageGraph(
  nodes: NodeEntry[],
  edges: EdgeEntry[]
): { flowNodes: Node<CatalogNodeData>[]; flowEdges: Edge[] } {
  const flowNodes: Node<CatalogNodeData>[] = nodes.map((entry) => ({
    id: entry.name,
    type: "catalog",
    position: { x: 0, y: 0 },
    data: { entry },
  }));

  const nodeIds = new Set(nodes.map((n) => n.name));
  const flowEdges: Edge[] = edges
    .filter((e) => nodeIds.has(e.source) && nodeIds.has(e.target))
    .map((e) => ({
      id: `${e.source}-${e.target}`,
      source: e.source,
      target: e.target,
      style: { stroke: "#555" },
    }));

  return layoutWithDagre(flowNodes, flowEdges);
}

function layoutWithDagre(
  nodes: Node<CatalogNodeData>[],
  edges: Edge[]
): { flowNodes: Node<CatalogNodeData>[]; flowEdges: Edge[] } {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: "LR", nodesep: 40, ranksep: 120 });

  nodes.forEach((node) => {
    g.setNode(node.id, { width: NODE_WIDTH, height: NODE_HEIGHT });
  });
  edges.forEach((edge) => {
    g.setEdge(edge.source, edge.target);
  });

  dagre.layout(g);

  const layoutedNodes = nodes.map((node) => {
    const pos = g.node(node.id);
    return {
      ...node,
      targetPosition: Position.Left,
      sourcePosition: Position.Right,
      position: {
        x: pos.x - NODE_WIDTH / 2,
        y: pos.y - NODE_HEIGHT / 2,
      },
    };
  });

  return { flowNodes: layoutedNodes, flowEdges: edges };
}

interface LineageGraphProps {
  nodes: NodeEntry[];
  edges: EdgeEntry[];
  layer?: string;
}

export default function LineageGraph({ nodes, edges, layer }: LineageGraphProps) {
  const filteredNodes = layer
    ? nodes.filter((n) => n.layer === layer)
    : nodes;

  const nodeNames = new Set(filteredNodes.map((n) => n.name));
  const filteredEdges = edges.filter(
    (e) => nodeNames.has(e.source) && nodeNames.has(e.target)
  );

  const { flowNodes, flowEdges } = buildLineageGraph(filteredNodes, filteredEdges);

  return (
    <div style={{ width: "100%", height: "80vh", background: "#0f0f23" }}>
      <ReactFlow
        nodes={flowNodes}
        edges={flowEdges}
        nodeTypes={catalogNodeTypes}
        fitView
        attributionPosition="bottom-left"
      >
        <MiniMap
          style={{ background: "#1a1a2e" }}
          maskColor="rgba(0,0,0,0.3)"
        />
        <Controls />
        <Background variant={BackgroundVariant.Dots} gap={12} size={1} color="#333" />
      </ReactFlow>
    </div>
  );
}
