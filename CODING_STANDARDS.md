# CODING_STANDARDS.md

This document defines the judgement calls and code quality standards enforced during review (e.g. by `/code-review`). Mechanical patterns, import rules, and type errors are enforced by automated checks rather than manual inspection.

---

## 1. General Principles

- **Locality & Simplicity**: Keep logic in one function unless composable or genuinely reused.
- **Single-Boundary Validation**: Validate unknown values once at the boundary that owns them. Pass typed values inward instead of repeating `typeof value === "object"` and property-existence checks. Do not defensively revalidate values already guaranteed by a schema, constructor, or internal type.
- **No Preemptive Abstractions**: Do not extract single-use helpers preemptively. Inline logic at the call site unless the helper is reused, hides a genuinely complex boundary, or has a clear independent name that improves caller readability.
- **Cost-Benefit on Edge Cases**: Before adding complexity for a speculative or vanishingly unlikely race or security edge case, explain the concrete failure mode, likelihood, and complexity cost to the user and get buy-in. Do not silently expand scope for theoretical robustness.
- **Predictable Error Propagation**: Avoid broad `try`/`catch` blocks where possible. Let framework error handlers and domain errors (`ZError`) handle failures predictably.
