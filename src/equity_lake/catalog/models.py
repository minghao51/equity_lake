"""Pydantic models for the data catalog."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ColumnInfo(BaseModel):
    name: str
    dtype: str
    nullable: bool = True
    description: str = ""


class DatasetEntry(BaseModel):
    name: str
    layer: str
    path: str
    description: str
    format: str = "delta"
    partition: str = "date="
    columns: list[ColumnInfo] = Field(default_factory=list)
    upstream: list[str] = Field(default_factory=list)
    downstream: list[str] = Field(default_factory=list)


class NodeEntry(BaseModel):
    name: str
    layer: str
    category: str = ""
    description: str = ""
    produces: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    validators: list[str] = Field(default_factory=list)
    tags: dict[str, Any] = Field(default_factory=dict)


class EdgeEntry(BaseModel):
    source: str
    target: str
    relationship: str = "computed_from"


class Catalog(BaseModel):
    version: str = "1.0"
    datasets: list[DatasetEntry] = Field(default_factory=list)
    nodes: list[NodeEntry] = Field(default_factory=list)
    edges: list[EdgeEntry] = Field(default_factory=list)
