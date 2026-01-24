# Documentation Structure

This document outlines the reorganized documentation structure as of 2025-01-24.

## 📚 Documentation Organization

### Root Documentation
- **README.md** - Project overview and quick start guide
- **claude.md** - AI assistant development guide

### docs/ - Technical Documentation

#### docs/implementations/ - Implementation Details
Technical documentation of specific features and implementations:
- `parallel-fetching.md` - Parallel market fetching implementation
- `final-implementation-summary.md` - Comprehensive summary of all improvements
- `cn-fetcher-optimization.md` - China A-share fetcher parallelization
- `config-refactoring.md` - Configuration system refactoring
- `efinance-migration.md` - efinance migration design document

#### docs/analytics/ - Performance & Testing
Test results and performance analysis:
- `test-results.md` - Comprehensive test results and validation
- (future) `performance-benchmarks.md` - Performance benchmarks

#### docs/guides/ - User & Developer Guides
How-to guides and usage documentation:
- `parallel-logging-guide.md` - Parallel fetching and structured logging guide

#### docs/planning/ - Project Planning
Historical planning documents:
- `implementation_plan.md` - Original implementation plan
- `development_log.md` - Development session logs

### docs/education/ - Educational Content

#### docs/education/concepts/ - Technical Concepts
Conceptual explanations of data engineering concepts:
- `data_pipeline_concepts.md` - ETL vs ELT, lakehouse architecture, partitioning

#### docs/education/research/ - Research Findings
Research on tools, libraries, and techniques:
- `tools-and-libraries-guide.md` - Tools and libraries guide (uv, yfinance, DuckDB, etc.)
- `libraries_and_techniques_research.md` - Ticker validation and incremental fetching research
- `ticker-validation-research.md` - Quick wins implementation research

### docs/archive/ - Archived Documentation
Old or deprecated documentation kept for historical reference:
- `old-implementation-summaries/` - Previous implementation summaries

## 📁 File Locations

### Implementation Docs
- Old: `docs/IMPLEMENTATION_SUMMARY.md` → New: `docs/implementations/parallel-fetching.md`
- Old: `docs/FINAL_IMPLEMENTATION_SUMMARY.md` → New: `docs/implementations/final-implementation-summary.md`
- Old: `docs/PARALLEL_CN_FETCHER.md` → New: `docs/implementations/cn-fetcher-optimization.md`
- Old: `plans/config_refactoring_summary.md` → New: `docs/implementations/config-refactoring.md`
- Old: `plans/2026-01-15-efinance-migration-design.md` → New: `docs/implementations/efinance-migration.md`

### Analytics & Testing
- Old: `docs/TEST_RESULTS.md` → New: `docs/analytics/test-results.md`

### Guides
- Old: `docs/IMPROVEMENTS_PARALLEL_LOGGING.md` → New: `docs/guides/parallel-logging-guide.md`

### Planning
- Old: `plans/implementation_plan.md` → New: `docs/planning/implementation_plan.md`
- Old: `plans/development_log.md` → New: `docs/planning/development_log.md`

### Education
- Old: `education/data_pipeline_concepts.md` → New: `docs/education/concepts/data_pipeline_concepts.md`
- Old: `education/tools_and_libraries.md` → New: `docs/education/research/tools-and-libraries-guide.md`
- Old: `plays/libraries_and_techniques_research.md` → New: `docs/education/research/libraries_and_techniques_research.md`
- Old: `plays/quick_wins_implementation.md` → New: `docs/education/research/ticker-validation-research.md`
- Old: `education/` → New: `docs/education/` (entire folder moved)

### Archived
- Old: `docs/implementations/parallel-fetching.md` → New: `docs/archive/old-implementation-summaries/parallel-fetching.md`

## 🗑️ Removed Directories
- `plans/` - Content moved to `docs/planning/` and `docs/implementations/`
- `plays/` - Content moved to `education/research/`

## 📝 Quick Reference

### Looking for implementation details?
→ Check `docs/implementations/`

### Looking for test results or performance data?
→ Check `docs/analytics/`

### Looking for how-to guides?
→ Check `docs/guides/`

### Looking for project history?
→ Check `docs/planning/`

### Looking to learn concepts?
→ Check `docs/education/concepts/`

### Looking for research on tools/techniques?
→ Check `docs/education/research/`

### Looking for old documentation?
→ Check `docs/archive/`
