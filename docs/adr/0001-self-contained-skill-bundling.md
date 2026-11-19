# Self-Contained Skill Bundling from Shared Monorepo Source

> Partly superseded by [ADR-0007](0007-registry-capability-distribution.md): no vendored third-party code, no GitHub self-updater, and local workbench modules ship once in the workbench package.

Development code is split between `shared/` (common gateway client, config, task/batch state, runtime lock, and updater) and `src/<modality>/` (modality-specific CLI, parameters, and local post-processing), and compiled/bundled into `skills/asynx-<modality>/scripts/` for distribution. This lets each Asset Skill be installed and executed as a standalone directory by any AI Agent without requiring monorepo path dependencies or a global package installation, while eliminating code duplication across skills during development.
