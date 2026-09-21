"""Every command this bot prints for a human to type or tap must resolve.

Prompted by a real incident on the sibling project (2026-09-05): Miri
printed an instruction, Ishay typed it exactly as shown, and it silently
misrouted to the wrong handler (document search, printing his blood
tests). A printed instruction that doesn't do what it says is worse than
no instruction — it looks authoritative and fails silently.

This bot's surface is structurally safer (Telegram `CommandHandler`
dispatch, not free-text intent classification), but "structurally safer"
is not "immune", and the check had never actually been written down. So:
every command named in `BotCommand` (the "/" menu) or quoted as an
example inside a message a household member actually sees must be a real,
registered `CommandHandler`. A rename on one side and not the other is
exactly the silent-miss shape from the incident — the difference here is
this test fails loudly, at commit time, instead of ships-and-a-human-
finds-out.
"""
import re
import unittest
from pathlib import Path

SOURCE = Path("grocery_bot/telegram_bot.py").read_text(encoding="utf-8")

# Every command Telegram will actually dispatch, extracted from the real
# registrations rather than hand-maintained — this list rots the moment
# it's copied instead of derived.
REGISTERED = set(re.findall(r'CommandHandler\("([a-z_]+)"', SOURCE))

# Every command offered in the "/" autocomplete menu. Since vNext Phase 2a
# (2026-09-21) the menu is the COMMAND_MENU table, not inline BotCommand
# literals, and it lists every registered command.
from grocery_bot.telegram_bot import COMMAND_MENU  # noqa: E402

MENU = {name for name, _ in COMMAND_MENU}

# Commands shown as a literal "/word ..." example inside a string a
# household member reads in chat (fallback prompts, help text) — checked
# by hand against the source, not derived, because deriving "what's a
# user-visible string" from source text reliably needs a real parser.
# Each was confirmed present verbatim on 2026-09-05, in telegram_bot.py
# except chaindeals (radar.py's stockup footer, covered separately by
# test_provenance.py's StockUpChainDealsFooter-style tests).
#
# "propose" was in this set until 2026-09-06: retired (used once ever,
# abandoned before its own redesign; /start_order supersedes it) —
# removed from both the menu and the /start help text, on purpose.
EXAMPLES_SHOWN_TO_USERS = {
    "price",          # "איזה מוצר לבדוק? למשל: /price חלב"
    "refresh_prices", # "הריצו /refresh_prices כדי למשוך..."
    "cheaper",        # "*/cheaper שניצלונים*" and "למשל: /cheaper שניצלונים"
    "list_full",      # "`/list_full core`" etc., four times
    "chaindeals",     # radar.py footer: "_עוד מבצעים...:_ /chaindeals"
}


class RegisteredCommandsAreReachableTests(unittest.TestCase):
    def test_at_least_the_known_commands_are_registered(self):
        # A floor, not a ceiling: catches the registration list itself
        # going missing (e.g. a bad refactor), not just individual drift.
        for cmd in ("start", "list", "price", "deals", "chaindeals",
                    "refresh_prices", "stockup", "cheaper",
                    "list_full", "digest", "start_order"):
            self.assertIn(cmd, REGISTERED, f"/{cmd} is not a CommandHandler")

    def test_every_menu_entry_is_actually_registered(self):
        # The "/" autocomplete offering a command Telegram won't dispatch
        # is the exact shape of the incident: it looks authoritative and
        # does nothing (or, worse, falls through to free text).
        for cmd in MENU:
            self.assertIn(cmd, REGISTERED, f"menu offers /{cmd}, which has no handler")

    def test_every_example_shown_to_a_household_member_is_registered(self):
        for cmd in EXAMPLES_SHOWN_TO_USERS:
            self.assertIn(cmd, REGISTERED, f"printed example /{cmd} has no handler")

    def test_chaindeals_is_both_registered_and_menu_listed(self):
        # The specific command born from an earlier trap (a t.me deep
        # link that arrived stripped) — must not regress to unlisted-only
        # or handler-less.
        self.assertIn("chaindeals", REGISTERED)
        self.assertIn("chaindeals", MENU)

    def test_no_command_is_unlisted_any_more(self):
        # price / deals / refresh_prices were deliberately kept off the
        # menu until 2026-09-21; the UX audit (gap #4: 21 commands, a
        # menu of 15, help covering fewer than half) reversed that. Every
        # registered command is now listed, and nothing listed is dead.
        self.assertEqual(REGISTERED, MENU)
        for cmd in ("price", "deals", "refresh_prices", "plan", "readiness"):
            self.assertIn(cmd, MENU)


if __name__ == "__main__":
    unittest.main()
