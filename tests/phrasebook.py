"""The ways the household actually asks for things.

One corpus, consumed by two very different test tiers, because the
capabilities split cleanly along a line that matters:

- **Resolution is deterministic and free.** Which mall, which merchant,
  which product — all of it is table lookup over harvested data. It can
  run on every commit, and a failure is a real failure.
- **Classification costs a model call and is not deterministic.** ~8s
  each, against Ishay's Claude subscription, and Miri measured the same
  message producing three different behaviours across three runs. A
  single green run proves very little.

So the free tier lives in `test_phrasebook.py` and runs with the suite;
the paid tier lives in `scripts/check_understanding.py`, is opt-in, and
repeats each phrasing to expose exactly that instability.

**What is asserted is the decision, never the wording.** The bot may
phrase its reply however it likes; what must hold is which mall it
resolved, which merchant it found, which intent it chose. Asserting on
reply text would make the suite fail on harmless rewording and teach
everyone to ignore it.

Phrasings here are meant to be awkward on purpose — prefixes, typos,
slang, half-sentences, English, and the same request said five ways.
A corpus of well-formed requests measures nothing, because well-formed
requests were never the problem.
"""

# --- Malls: the six modelled sites, said however a person says them ---
# (phrase, expected canonical mall name or "" for "must not resolve")
MALLS = [
    ("יש לי הנחה בקניון רמת אביב", "קניון רמת אביב, תל אביב"),
    ("רמת אביב", "קניון רמת אביב, תל אביב"),
    ("הקניון ברמת אביב", "קניון רמת אביב, תל אביב"),
    ("מה יש לי ברמת אביב", "קניון רמת אביב, תל אביב"),
    ("אני עכשיו בקניון רמת אביב, מה כדאי", "קניון רמת אביב, תל אביב"),
    ("ramat aviv", "קניון רמת אביב, תל אביב"),
    ("דיזנגוף סנטר", "דיזנגוף סנטר, תל אביב"),
    ("דיזינגוף סנטר", "דיזנגוף סנטר, תל אביב"),
    ("דיזנגוף", "דיזנגוף סנטר, תל אביב"),
    ("dizengoff center", "דיזנגוף סנטר, תל אביב"),
    ("אילו חנויות בהנחה בקניון עזריאלי", "קניון עזריאלי, תל אביב"),
    ("עזריאלי", "קניון עזריאלי, תל אביב"),
    ("azrieli", "קניון עזריאלי, תל אביב"),
    ("ביג פאשן גלילות", "ביג פאשן גלילות"),
    ("ביג גלילות", "ביג פאשן גלילות"),
    ("גלילות", "ביג פאשן גלילות"),
    ("אני בגלילות", "ביג פאשן גלילות"),
    ("big glilot", "ביג פאשן גלילות"),
    ("קניון שבעת הכוכבים", "קניון שבעת הכוכבים, הרצליה"),
    ("שבעת הכוכבים", "קניון שבעת הכוכבים, הרצליה"),
    ("בשבעת הכוכבים הרצליה", "קניון שבעת הכוכבים, הרצליה"),
    ("7 הכוכבים", "קניון שבעת הכוכבים, הרצליה"),
    ("seven stars", "קניון שבעת הכוכבים, הרצליה"),
    ("קניון TLV", "TLV פאשן מול (גינדי), תל אביב"),
    ("גינדי", "TLV פאשן מול (גינדי), תל אביב"),
    ("פאשן מול", "TLV פאשן מול (גינדי), תל אביב"),
    ("tlv fashion mall", "TLV פאשן מול (גינדי), תל אביב"),
    # Must refuse rather than answer with the wrong branch of a brand.
    ("קניון עזריאלי חיפה", ""),
    ("עזריאלי ירושלים", ""),
    ("שבעת הכוכבים אילת", ""),
    # Malls we simply do not model. "I don't know that one" is correct.
    ("יש לי הנחה בקניון מלחה", ""),
    ("קניון איילון", ""),
    ("קניון בראשון לציון", ""),
    ("קניון הזהב", ""),
    ("בסופר", ""),
    ("", ""),
]

# --- Merchants: a benefit-carrying chain, named loosely ---
# (phrase, a merchant name that must appear among the results)
MERCHANTS = [
    ("פוקס", "פוקס"),
    ("נייק", "נייק"),
    ("אדידס", "אדידס"),
    ("מקדונלדס", "מקדונלד"),
    ("סופר פארם", "פארם"),
    ("גולף", "גולף"),
    ("אופטיקנה", "אופטיקנה"),
    ("שילב", "שילב"),
    ("דומינוס", "דומינוס"),
]

# Major Israeli chains that carry **no** behatsdaa benefit. Verified
# against the raw catalogue on 2026-09-10: zero occurrences, so an empty
# result is correct and the honest answer is "no benefit for this chain",
# never "I couldn't find it". The first run of this corpus caught קסטרו
# sitting in the list above on my assumption that it must be there.
MERCHANTS_ABSENT = ["קסטרו", "רנואר"]

# --- Food and supermarket terms, as typed into a phone ---
# Each must return at least one priced product. Slang, misspellings and
# bare nouns included on purpose.
FOOD = [
    "חלב", "לחם", "ביצים", "קוטג'", "קוטג", "גבינה צהובה", "עגבניות",
    "מלפפונים", "בננות", "שמן זית", "אורז", "פסטה", "טונה", "חומוס",
    "יוגורט", "חמאה", "סוכר", "קמח", "תירס", "נייר טואלט",
]

# --- Intent classification. Paid tier. ---
# (phrase, acceptable intents). More than one is allowed where a human
# would also hesitate — the test is "did it land somewhere defensible",
# not "did it match my favourite label".
INTENTS = [
    ("תוסיף חלב", {"add_item"}),
    ("צריך לחם וביצים", {"add_item"}),
    ("חלב", {"add_item"}),
    ("תוסיף בבקשה קוטג' ולחם לרשימה", {"add_item"}),
    ("נגמר לנו החלב", {"add_item"}),
    ("תוריד את הלחם מהרשימה", {"remove_item"}),
    ("לא צריך יותר עגבניות", {"remove_item"}),
    ("כמה עולה חלב", {"price_query"}),
    ("מה המחיר של קוטג'", {"price_query"}),
    ("יש מבצע על שמן זית?", {"price_query", "deals"}),
    ("מה יש במבצע", {"deals"}),
    ("מה יש ברשימה", {"show_list"}),
    ("תראה לי את הרשימה", {"show_list"}),
    ("מתכון לשקשוקה", {"recipe"}),
    ("מה צריך בשביל פסטה ברוטב רוזה", {"recipe"}),
    ("תכנן לי תפריט שבועי", {"meal_plan"}),
    ("מה נאכל השבוע?", {"meal_plan"}),
    ("זרקתי חצי מהחסה", {"report_waste"}),
    ("התקלקלו לנו העגבניות", {"report_waste"}),
    ("סיימתי קנייה", {"shopped"}),
    ("הזמנתי משופרסל", {"shopped"}),
    ("תזמין עכשיו הכל", {"start_order"}),
    ("מלא את העגלה", {"start_order"}),
    ("תוסיף חלב לעגלה", {"add_to_cart"}),
    ("תודה", {"smalltalk"}),
    # Genuinely ambiguous. `unclear` is the right answer, and after the
    # loop it should arrive with a question rather than a shrug.
    ("מה", {"unclear"}),
    ("נגמר", {"unclear"}),
    ("הזה", {"unclear"}),
]

# Messages that must never come back as a cart-touching intent from the
# second pass. The classifier may still classify a clear request; what is
# forbidden is the *loop* inventing one out of an unparseable message.
LOOP_MUST_NOT_ACT = [
    "נגמר", "הזה", "מה", "תעשה מה שצריך", "אתה יודע מה לעשות",
]
