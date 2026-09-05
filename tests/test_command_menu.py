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

# Every command offered in the "/" autocomplete menu.
MENU = set(re.findall(r'BotCommand\("([a-z_]+)"', SOURCE))

# Commands shown as a literal "/word ..." example inside a string a
# household member reads in chat (fallback prompts, help text) — checked
# by hand against the source, not derived, because deriving "what's a
# user-visible string" from source text reliably needs a real parser.
# Each was confirmed present verbatim on 2026-09-05, in telegram_bot.py
# except chaindeals (radar.py's stockup footer, covered separately by
# test_provenance.py's StockUpChainDealsFooter-style tests).
EXAMPLES_SHOWN_TO_USERS = {
    "price",          # "איזה מוצר לבדוק? למשל: /price חלב"
    "refresh_prices", # "הריצו /refresh_prices כדי למשוך..."
    "cheaper",        # "*/cheaper שניצלונים*" and "למשל: /cheaper שניצלונים"
    "propose",        # "*/propose* — הצעה לפי מחלקות..."
    "list_full",      # "`/list_full core`" etc., four times
    "chaindeals",     # radar.py footer: "_עוד מבצעים...:_ /chaindeals"
}


class RegisteredCommandsAreReachableTests(unittest.TestCase):
    def test_at_least_the_known_commands_are_registered(self):
        # A floor, not a ceiling: catches the registration list itself
        # going missing (e.g. a bad refactor), not just individual drift.
        for cmd in ("start", "list", "price", "deals", "chaindeals",
                    "refresh_prices", "propose", "stockup", "cheaper",
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

    def test_deliberately_unlisted_commands_still_work_when_typed(self):
        # price / deals / refresh_prices are intentionally left off the
        # menu (see _register_bot_metadata's own comment) so the menu
        # stays short — but "unlisted" must never silently mean
        # "unregistered". Typing them by hand must still dispatch.
        for cmd in ("price", "deals", "refresh_prices"):
            self.assertNotIn(cmd, MENU, f"/{cmd} was expected to stay off the menu")
            self.assertIn(cmd, REGISTERED, f"/{cmd} is unlisted AND unregistered — dead command")


if __name__ == "__main__":
    unittest.main()
