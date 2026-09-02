"""Route a battery of realistic Hebrew phrasings through the live NLU.

Not a unit test: this calls the real `claude` CLI, the same way the bot
does, to find out whether routing is *consistent* rather than whether it
works once. Each case is run twice, because an intermittent misroute is
the failure mode that matters — a phrasing that works when you test it
and files a meal plan as a grocery item on a Tuesday.
"""
import concurrent.futures
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from grocery_bot.nlu import parse_message

CASES = [
    # (message, expected intent)
    ("תכנן לי תפריט לשבוע", "meal_plan"),
    # Indirect phrasings. "מה נאכל השבוע?" flipped between meal_plan and
    # unclear on 2026-09-02 — the prompt covered only the imperative
    # forms, and the "prefer unclear over guessing" rule caught the
    # question form. That is exactly the failure this harness exists to
    # find: it passed on the first run and failed on the second.
    ("מה נאכל השבוע?", "meal_plan"),
    ("מה עושים לארוחות ערב?", "meal_plan"),
    ("אין לי מושג מה לבשל השבוע", "meal_plan"),
    ("תבנה לי תפריט שבועי בלי בשר אדום", "meal_plan"),
    ("תכין לי תפריט לשבוע הבא, משהו קליל", "meal_plan"),
    ("אני רוצה תפריט שבועי", "meal_plan"),
    ("מתכון לשקשוקה", "recipe"),
    ("מה צריך בשביל לזניה", "recipe"),
    ("תוסיף מה שצריך לעוגת שוקולד", "recipe"),
    ("תכין רשימה לחומוס ביתי", "recipe"),
    ("תוסיף חלב", "add_item"),
    ("צריך לחם ועגבניות", "add_item"),
    ("חלב", "add_item"),
    ("תוסיף 2 קילו עגבניות שרי", "add_item"),
    ("תוריד את הקטשופ מהרשימה", "remove_item"),
    ("לא צריך יותר את המעדנים", "remove_item"),
    ("כמה עולה קוטג", "price_query"),
    ("מה המחיר של שמן זית", "price_query"),
    ("יש מבצע על חלב?", "price_query"),
    ("מה יש במבצע", "deals"),
    ("מה יש ברשימה", "show_list"),
    ("תראה לי את הרשימה", "show_list"),
    ("תוסיף קוטג לעגלה", "add_to_cart"),
    ("תכניס לחם לסל", "add_to_cart"),
    ("תעדכן את העגלה עם חלב וגבינה", "add_to_cart"),
    ("תתחיל הזמנה", "start_order"),
    ("מלא את העגלה", "start_order"),
    ("תעדכן את הסל", "start_order"),
    ("זרקתי חצי חסה ושתי עגבניות", "report_waste"),
    ("התקלקל לנו הקוטג", "report_waste"),
    ("מה", "unclear"),
    ("תודה", "smalltalk"),
]


def run(case):
    message, expected = case
    got = []
    for _ in range(2):
        parsed = parse_message(message)
        got.append(
            (parsed.intent, parsed.used_fallback, [i.name for i in parsed.items], parsed.query)
        )
    return message, expected, got


with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
    results = list(pool.map(run, CASES))

bad = 0
for message, expected, got in results:
    intents = [g[0] for g in got]
    ok = all(i == expected for i in intents)
    stable = len(set(intents)) == 1
    if not ok or not stable:
        bad += 1
        flag = "MISROUTE" if not ok else "UNSTABLE"
        print(f"{flag:9s} {message!r}")
        print(f"          expected {expected}, got {intents}")
        for intent, fb, items, query in got:
            print(f"          -> intent={intent} fallback={fb} items={items} query={query!r}")
    else:
        fb = any(g[1] for g in got)
        print(f"{'ok':9s} {message!r} -> {expected}" + ("  (FELL BACK)" if fb else ""))

print(f"\n{len(CASES) - bad}/{len(CASES)} routed correctly and stably")
