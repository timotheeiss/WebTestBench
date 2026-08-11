"""Baseline arm: perceive via the Playwright accessibility snapshot.

Everything not specific to that channel lives in _defect_detection_core.
"""
from ._defect_detection_core import TARGET_CONTRACT, build

CHANNEL_INTRO = (
    "You perceive the page through the **Playwright MCP**'s accessibility "
    "snapshot, and act through the same MCP."
)

CHANNEL_OBSERVE = """- `browser_snapshot()` → the accessibility tree of the current page as YAML. Every interactive node carries a handle of the form `[ref=eNNN]`:

```yaml
- link "Admin Panel" [ref=e461] [cursor=pointer]:
    - /url: /admin
- searchbox "Search products..." [ref=e21]
- button "Add Product" [ref=e30]
```

- The tree covers the whole page, so it is large. Take one per screen and read from it, rather than re-taking it to look something up again."""

CHANNEL_ADDRESSING = """%s

Take the ref from the snapshot and pass the **bare** ref as `target` — just the `eNNN` from inside `[ref=…]`:

- Click a link: `browser_click` with `target: "e461"`, `element: "Admin Panel link"`.
- Fill one input: `browser_type` with `target: "e21"`, `text: "laptop"`.
- Fill a whole form in ONE call: `browser_fill_form` with a `fields` list where each field's `target` is a bare ref.

Do **not** pass the brackets (`"[ref=e461]"`) or the whole snapshot line (`"link \\"Admin Panel\\" [ref=e461]"`) — neither resolves. A unique CSS selector is also accepted as a `target` where you have one.

Refs are assigned per snapshot: if the page has re-rendered since you took it, take a fresh `browser_snapshot` rather than reusing an old ref.""" % TARGET_CONTRACT

PROMPT_DEFECT_DETECTION_BASED_GOLD = build(
    channel_intro=CHANNEL_INTRO,
    channel_observe=CHANNEL_OBSERVE,
    channel_addressing=CHANNEL_ADDRESSING,
    observe_call="`browser_snapshot`",
    channel_tool_use="**Playwright MCP**.",
    channel_extra_rules="",
)
