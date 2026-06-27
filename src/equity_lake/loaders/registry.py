"""Registry for built-in and third-party data loaders."""

from __future__ import annotations

from equity_lake.loaders.base import BaseDataLoader, LoaderMetadata


class LoaderRegistry:
    """Simple plugin registry for built-in data loaders."""

    def __init__(self) -> None:
        self._loaders: dict[str, type[BaseDataLoader]] = {}

    def register(self, name: str, loader_class: type[BaseDataLoader]) -> None:
        """Register a loader class."""
        if not issubclass(loader_class, BaseDataLoader):
            raise TypeError("Loader must inherit from BaseDataLoader")
        self._loaders[name] = loader_class

    def get(self, name: str) -> type[BaseDataLoader]:
        """Get a loader class by name."""
        if name not in self._loaders:
            available = ", ".join(sorted(self._loaders))
            raise KeyError(f"Loader '{name}' not found. Available: {available}")
        return self._loaders[name]

    def create(self, name: str, config: dict[str, object] | None = None) -> BaseDataLoader:
        """Instantiate a loader."""
        return self.get(name)(config=config or {})

    def list(self) -> list[LoaderMetadata]:
        """List loader metadata."""
        return [loader.metadata for loader in self._loaders.values()]


registry = LoaderRegistry()


__all__ = ["LoaderRegistry", "registry"]
