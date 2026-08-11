"""Hinted arm: perceive via the Semantic Hints MCP, act via Playwright.

Everything not specific to that channel lives in _defect_detection_core.
"""
from ._defect_detection_core import TARGET_CONTRACT, build

CHANNEL_INTRO = (
    "This application has been annotated with **semantic hints** "
    "(`data-semtag-*` attributes), and you have a dedicated **Semantic Hints "
    "MCP** that reads them. You perceive the page through the Semantic Hints "
    "MCP and act through the **Playwright MCP**: *observe with Semantic Hints, "
    "act with Playwright.*"
)

CHANNEL_OBSERVE = """- `semantic_snapshot({ "url"?, "scope"?, "includeHidden"? })` → a compact map of the hinted elements on the current page, **grouped by role**: `navigation`, `action`, `option`, `input`, `select`, `toggle`, `slider`, `observable`, `region`, `collection` — plus `other` and `collections`. The group key IS the element's role, so entries carry no `role` field; empty groups are omitted. Each element carries a stable `id` (its `data-semtag-id`), a `name`, and where relevant `value`, `state`, `enabled`, `target`, `controls`, `options`, `visible`.
- A `select` entry's `options` field lists its `{ "value", "label" }` pairs. Each option is individually addressable as `<trigger-id>.option.<value>`.
- `other` holds elements whose hint is missing or off-vocabulary. They are addressable like any other element, but the app never said what they are, so treat them with suspicion and fall back to `browser_snapshot` if one matters to a checklist item.
- `semantic_observe({ "id" })` → the current compact value/state of ONE hinted element, resolved by `data-semtag-id`.

### Reading a folded collection

Lists and grids arrive **folded** in `collections` — shared attributes stated once, each item one row:

```jsonc
{ "id": "products.grid",
  "idPattern": "products.grid.item.{key}[.{control}]",
  "item":         { "role": "navigation" },
  "itemControls": { "price": { "role": "observable", "state": "product.price" },
                    "buy":   { "role": "action", "action": "buy-product" } },
  // (a folded member has no group key around it, so its role is stated here)
  "fields": ["key", "name", "target", "price.value", "buy"],
  "items": [ ["alpha", "Alpha Phone", "product.detail", "£10.00", true],
             ["beta",  "Beta Laptop", "product.detail", "£20.00", null] ] }
```

- `fields` is the column header for `items`; read a row by zipping it against `fields`. A `null` cell means that item does not have that element — above, `beta` has no Buy button.
- Fields in `item` / `itemControls` apply to **every** item — they are not repeated per row.
- **Every folded element is still individually clickable and observable.** Substitute into `idPattern`: item `beta` is `[data-semtag-id='products.grid.item.beta']`, its price is `products.grid.item.beta.price`, its buy button `products.grid.item.beta.buy`.

This output is far smaller than a full accessibility tree. Both MCPs observe and control the **same browser page**, so a `semantic_snapshot` reflects exactly what Playwright is acting on."""

CHANNEL_ADDRESSING = """%s

Pick the element's `id` from `semantic_snapshot` and pass the stable selector `[data-semtag-id='<id>']` as `target` — no `browser_snapshot` and no element ref needed:

- Click an action: `browser_click` with `target: "[data-semtag-id='checkout.submit']"`, `element: "Checkout submit button"`.
- Fill one input: `browser_type` with `target: "[data-semtag-id='filters.search']"`, `text: "laptop"`.
- Fill a whole form in ONE call: `browser_fill_form` with a `fields` list where each field's `target` is a `[data-semtag-id='<id>']` selector.

These ids are stable across re-renders, so a selector stays valid for as long as the element is on the page.

For an element with **no** `data-semtag-id`, take a `browser_snapshot` and pass the bare ref from `[ref=eNNN]` instead (e.g. `target: "e461"`, not `"[ref=e461]"` and not the whole snapshot line). Refs are assigned per snapshot, so re-snapshot if the page has re-rendered.""" % TARGET_CONTRACT

CHANNEL_EXTRA_RULES = 
"""- Fallback: If a checklist item concerns an element with no `data-semtag-id`, or `semantic_snapshot`/`semantic_observe` is insufficient, fall back to `browser_snapshot` for that item only.
- Hints are descriptive, not an oracle: `data-semtag-*` describe what the UI *is/does*; they never tell you whether a test passes. Always judge actual behavior against your own inferred expectation."""

PROMPT_DEFECT_DETECTION_BASED_GOLD_WITH_HINTS = build(
    channel_intro=CHANNEL_INTRO,
    channel_observe=CHANNEL_OBSERVE,
    channel_addressing=CHANNEL_ADDRESSING,
    observe_call="`semantic_snapshot`",
    channel_tool_use=(
        "**Playwright MCP** (actions / "
        "fallback inspection) and the **Semantic Hints MCP** (observation)."
    ),
    channel_extra_rules=CHANNEL_EXTRA_RULES,
)
