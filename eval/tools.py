# https://github.com/microsoft/playwright-mcp

PLAYWRIGHT_MCP_PACKAGE = "@playwright/mcp@0.0.76"
PLAYWRIGHT_CLIPBOARD_PERMISSIONS = ["clipboard-read", "clipboard-write"]
PLAYWRIGHT_SCREENSHOT_TOOL = "mcp__playwright__browser_take_screenshot"
PLAYWRIGHT_UNSAFE_CODE_TOOL = "mcp__playwright__browser_run_code_unsafe"

# Claude Code's ``allowed_tools`` option controls permission approval; it does
# not hide the rest of Claude Code's built-in tools.  Experimental browser
# sessions therefore expose only ToolSearch from the built-in set (needed to
# load deferred MCP tools) and explicitly deny tools that could access the
# filesystem, shell, network, skills, or subagents.
EXPERIMENT_BUILTIN_TOOLS = ["ToolSearch"]
FORBIDDEN_EXPERIMENT_TOOLS = [
    "Bash",
    "Read",
    "Write",
    "Edit",
    "MultiEdit",
    "Grep",
    "Glob",
    "Agent",
    "Task",
    "Skill",
    "TodoWrite",
    "WebFetch",
    "WebSearch",
    "NotebookEdit",
    "EnterPlanMode",
    "ExitPlanMode",
    "AskUserQuestion",
    # Playwright MCP 0.0.76 describes this tool as RCE-equivalent. Keep it out
    # of experiment sessions even though it is part of the MCP's core schema.
    PLAYWRIGHT_UNSAFE_CODE_TOOL,
]


# Safe core and tab-management tools exposed by the pinned Playwright MCP
# version above. Opt-in capability tools (vision coordinates, storage,
# network interception, PDF generation, and test assertions) are deliberately
# absent because the runner does not enable their corresponding ``--caps``.
PlaywrightTools = [
    ## Browser navigation & window control
    "mcp__playwright__browser_navigate",          # Navigate to a URL
    "mcp__playwright__browser_navigate_back",     # Go back to the previous page in the history
    "mcp__playwright__browser_close",             # Close the page
    "mcp__playwright__browser_resize",            # Resize the browser window
    "mcp__playwright__browser_tabs",               # List, create, close, or select a browser tab

    ## Page interaction
    "mcp__playwright__browser_click",          # Perform click on a web page
    "mcp__playwright__browser_type",           # Type text into editable element
    "mcp__playwright__browser_press_key",      # Press a key on the keyboard
    "mcp__playwright__browser_hover",          # Hover over element on page
    "mcp__playwright__browser_drag",           # Perform drag and drop between two elements
    "mcp__playwright__browser_drop",           # Drop files onto a page target
    "mcp__playwright__browser_select_option",  # Select an option in a dropdown
    "mcp__playwright__browser_fill_form",      # Fill multiple form fields
    "mcp__playwright__browser_handle_dialog",  # Handle a dialog
    "mcp__playwright__browser_file_upload",    # Upload one or multiple known files

    ## Page inspection
    "mcp__playwright__browser_snapshot",  # Capture accessibility snapshot of the current page, this is better than screenshot
    "mcp__playwright__browser_console_messages",  # Returns all console messages
    "mcp__playwright__browser_network_requests",  # Returns all network requests since loading the page
    "mcp__playwright__browser_network_request",   # Returns details for one recorded request

    ## Advanced actions
    "mcp__playwright__browser_evaluate",       # Evaluate JavaScript expression on page or element
    "mcp__playwright__browser_wait_for",       # Wait for text to appear or disappear or a specified time to pass
]


def playwright_tools(allow_screenshots: bool = False) -> list[str]:
    """Return the live Playwright tool allowlist for one run."""
    if allow_screenshots:
        return PlaywrightTools + [PLAYWRIGHT_SCREENSHOT_TOOL]
    return list(PlaywrightTools)


def experiment_tool_permissions(
    mcp_tools: list[str],
    *,
    allow_screenshots: bool = False,
) -> tuple[list[str], list[str]]:
    """Return explicit allow/deny lists for an experimental browser session.

    ``tools=EXPERIMENT_BUILTIN_TOOLS`` is what makes the policy deny-by-default
    for Claude Code built-ins.  These lists separately configure permissions:
    MCP tools plus ToolSearch are pre-approved, while known out-of-protocol
    tools are explicitly denied as defence in depth.
    """
    allowed = list(dict.fromkeys(EXPERIMENT_BUILTIN_TOOLS + mcp_tools))
    denied = list(FORBIDDEN_EXPERIMENT_TOOLS)
    if not allow_screenshots:
        denied.append(PLAYWRIGHT_SCREENSHOT_TOOL)
    return allowed, denied
