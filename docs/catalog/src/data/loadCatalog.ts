import * as fs from "node:fs";
import * as path from "node:path";
import { parseCatalog, type Catalog } from "./catalog";

export function loadCatalog(): Catalog {
  const jsonlPath = path.resolve("../../data/catalog.jsonl");
  const jsonl = fs.readFileSync(jsonlPath, "utf-8");
  return parseCatalog(jsonl);
}
