# About this fork

This is a fork of **WebTestBench**, a benchmark where an LLM agent drives a web app (via Playwright, reading the accessibility tree) to detect UI defects against a checklist.

## Research aim

Make web-testing agents **more efficient** (fewer tokens, lower latency) by injecting **semantic grounding hints** into the app code — `data-*` attributes on key entities (e.g. product cards) plus a small manifest of selectors and invariants. The agent then reads typed domain state with targeted `browser_evaluate` queries instead of dumping the full, noisy accessibility snapshot on every step.

**Guardrail:** hints accelerate *locating and reading* state; the pass/fail **verdict always comes from observed live state**, never from the metadata — so the speedup can't hide bugs. The goal is equal-or-better defect recall at materially lower cost.
