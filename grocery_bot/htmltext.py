"""Telegram HTML, replacing the legacy-Markdown escape machinery.

Why the container changed, rather than the escaping getting cleverer.

Legacy Markdown has no backslash escape, so store-supplied text could
never be made safe *inside* an entity — only kept outside one. That cost
this project real money twice: a cross-chain deals button whose handler
ran and whose send failed silently, and an order summary that vanished
after both carts had been filled, taking 39 unanswered disambiguation
questions with it.

The budget project hit the same wall from the other side and drew the
conclusion first (`family-budget-automation/src/report/render-html.js`):

    "Markdown was the wrong container for this. Hebrew prose interleaved
    with Latin-script numbers, currency signs and ** markers has no
    reliable direction... The fix is not better Markdown; it is a
    container that declares its direction."

In HTML the whole class disappears. Escaping is three characters, and
`*`, `_`, backtick, `[` and `]` carry no meaning at all — so they travel
through untouched.

**That last part is a data fix, not only a bug fix.** `mdtext.safe_name`
had to rewrite product names to survive: "בירה קרומבאכר 6*330 מ\"ל"
reached the household as "6×330", and underscores and brackets became
spaces. Every one of those was a silent edit to a real product name.
Here the name arrives as the shop wrote it.
"""
from __future__ import annotations

import html

# Telegram's HTML parser assigns meaning to exactly these three.
# Everything else — including every character mdtext had to defend
# against — is ordinary text.
_ESCAPES = (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"))


def escape(text) -> str:
    """Make store-supplied text safe anywhere in an HTML message.

    Safe *inside* an entity as well as beside one, which legacy Markdown
    could never offer. `html.escape` is not used directly because its
    default also converts quotes, which is unnecessary here and makes
    Hebrew product names carrying a geresh harder to read.
    """
    if not text:
        return ""
    out = str(text)
    for char, entity in _ESCAPES:
        out = out.replace(char, entity)
    return out


def bold(text) -> str:
    """Bold, with the content escaped — the pairing that used to fail."""
    return f"<b>{escape(text)}</b>"


def italic(text) -> str:
    return f"<i>{escape(text)}</i>"


def code(text) -> str:
    return f"<code>{escape(text)}</code>"


def link(text, url: str) -> str:
    return f'<a href="{escape(url)}">{escape(text)}</a>'


def blockquote(text, expandable: bool = False) -> str:
    """A collapsible quote — Bot API 8.3, already available to us.

    Worth having for the long messages: the digest and the cross-chain
    deals list are the two that a phone screen cannot hold, and an
    expandable quote lets them arrive whole without burying what came
    after them.
    """
    tag = "<blockquote expandable>" if expandable else "<blockquote>"
    return f"{tag}{escape(text)}</blockquote>"
