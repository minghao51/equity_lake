# Documentation Index

This directory contains all project documentation organized by purpose.

## Quick Navigation

### [Implementations](implementations/)
Technical implementation details and feature documentation:
- [Parallel CN Fetcher](implementations/parallel-cn-fetcher.md) - CN fetcher optimization
- [Ticker Config Refactoring](implementations/ticker-config-refactoring.md) - Config system refactoring
- [Efinance Migration](implementations/efinance-migration.md) - Data source migration
- [Implementation Summary v0.2](implementations/implementation-summary-v0.2.md) - Complete v0.2.0 summary

### [Analytics](analytics/)
Performance metrics and test results:
- [Test Results](analytics/test-results.md) - Test results & validation

### [Guides](guides/)
User and developer guides:
- [User Guide](guides/user-guide.md) - Comprehensive usage documentation
- [Parallel Fetching Guide](guides/parallel-fetching-guide.md) - Parallel fetching & structured logging

### [Planning](planning/)
Project planning and historical documents:
- [Implementation Plan](planning/implementation-plan.md) - Original implementation plan
- [Development Log](planning/development-log.md) - Development session logs

### [Education](education/)
Educational content for learning and reference:

#### [Concepts](education/concepts/)
Technical concepts and architecture:
- [Data Pipeline Concepts](education/concepts/data-pipeline-concepts.md) - ETL vs ELT, lakehouse, partitioning

#### [Research](education/research/)
Research findings on tools and techniques:
- [Tools Guide](education/research/tools-guide.md) - uv, yfinance, DuckDB, etc.
- [Incremental Fetching Research](education/research/incremental-fetching-research.md) - Gap detection & incremental fetching
- [Ticker Validation Research](education/research/ticker-validation-research.md) - Ticker validation techniques

### [Archive](archive/)
Historical documentation kept for reference:
- [Old Implementation Summaries](archive/old-implementation-summaries/) - Previous implementation docs

## Documentation Structure

```
docs/
├── implementations/    # Technical implementation docs
├── analytics/          # Performance & testing
├── guides/             # User & developer guides
├── planning/           # Project planning
├── education/          # Learning resources
│   ├── concepts/       # Technical concepts
│   └── research/       # Research findings
└── archive/            # Historical docs
```

## For New Users

1. Start with the main [README.md](../README.md)
2. Read the [User Guide](guides/user-guide.md) for detailed usage
3. Explore [Education/Concepts](education/concepts/) to understand the architecture

## For Developers

1. Check [Implementations](implementations/) for technical details
2. Review [Planning](planning/) for project history
3. Consult [Research](education/research/) for optimization opportunities

## For Contributors

1. Read the [Implementation Plan](planning/implementation-plan.md)
2. Review [Development Log](planning/development-log.md)
3. Check [Test Results](analytics/test-results.md) for current status
