# אוטומציית קניות בסופר אונליין

> **Claude Code working in this repo: read [`CLAUDE.md`](./CLAUDE.md) first.**

כלי אישי (לא מוצר) שממלא עגלה אמיתית בשופרסל (ובעתיד טיב טעם) על סמך
רשימת מוצרים קבועה + בקשות אד-הוק שנשלחות בטלגרם. לרקע המלא, ההחלטות
וההיקש שהוביל לארכיטקטורה — ראו [`GOALS.md`](./GOALS.md).

## מצב נוכחי: Phase 1

| רכיב | מצב |
|---|---|
| רשימת בסיס + אחסון (SQLite) | ✅ ממומש ובדוק (`tests/`) |
| בוט טלגרם (הפעלה ידנית, קליטת אד-הוק, פתרון עמימות) | ✅ ממומש, לא נבדק מול טלגרם אמיתי |
| מתאם שופרסל (Playwright) | ⚠️ ממומש אך **לא נבדק מול האתר האמיתי** — ראו "כוונון" למטה |
| מתאם טיב טעם | ❌ Phase 2 |
| מתכונים/תזונאית, תכנון ארוחות | ❌ Phase 2 |
| השוואת מחירים/מותגים | ❌ Phase 3 |

הקוד נכתב בלי גישה לחשבון שופרסל אמיתי או לטוקן בוט טלגרם, אז שני
הדברים האלה הם צעדים חד-פעמיים שרק אתה יכול להשלים (ראו למטה).

## שלב 0: להעביר את העבודה לשרת Contabo

כל העבודה עד כה נעשתה ב-Claude Code cloud session, שלא יכול לגעת
באינטרנט האמיתי (חסום ל-shufersal.co.il ול-api.telegram.org) וגם זמני
מטבעו. הצעד הבא הוא לפתוח session של Claude Code **ישירות על שרת
ה-Contabo** (למשל דרך אפליקציית SSH באייפון) — משם יש גישה רגילה
לאינטרנט, וה-session נשאר "מחובר" (persistent) כל עוד השרת דלוק,
בדיוק כמו sessions אחרים שרצים לכם על אותו שרת.

```bash
ssh <user>@<contabo-ip>
git clone <repo-url> grocery-automation   # אם עוד לא משוכפל שם
cd grocery-automation
git checkout claude/online-grocery-automation-b7pq4g
claude   # אם Claude Code כבר מותקן שם מפרויקטים קודמים
```

ה-session החדש קורא אוטומטית את [`CLAUDE.md`](./CLAUDE.md) ו-
[`GOALS.md`](./GOALS.md) ומקבל את כל ההקשר — אין צורך להסביר שוב
כלום. משם ממשיכים ישירות לצעדי ההתקנה וה-login החד-פעמי למטה.

## התקנה

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env   # ואז למלא TELEGRAM_BOT_TOKEN
```

### שלב חד-פעמי 1: יצירת בוט טלגרם

1. פותחים שיחה עם [@BotFather](https://t.me/BotFather) בטלגרם.
2. `/newbot`, נותנים שם (למשל "קניות הבית").
3. מעתיקים את הטוקן שמתקבל ל-`TELEGRAM_BOT_TOKEN` בקובץ `.env`.
4. שולחים הודעה כלשהי לבוט כדי לקבל את ה-`user_id` שלכם (למשל דרך
   [@userinfobot](https://t.me/userinfobot)) ומכניסים את שני ה-ID-ים
   (שלך ושל לירן) ל-`ALLOWED_TELEGRAM_USER_IDS` (מופרד בפסיקים).

### שלב חד-פעמי 2: התחברות לשופרסל

השרת (Contabo VPS) רץ headless (בלי מסך מחובר), אז ההתחברות הראשונית
חייבת לקרות במקום עם דפדפן אמיתי — לא ניתן לעשות זאת ישירות מהאייפון.
הפתרון: desktop וירטואלי זמני על השרת עצמו, שנצפה דרך הדפדפן באייפון
מעל מנהרת SSH.

```bash
# על השרת (למשל בתוך session ssh מהאייפון):
./scripts/setup_remote_desktop.sh
```

הסקריפט מתקין (apt) ומריץ Xvfb + fluxbox + x11vnc + noVNC, הכל מאזין
על `127.0.0.1` בלבד — לא חשוף לאינטרנט. משם:

1. במנהרת SSH מהאייפון (רוב אפליקציות ה-SSH, כמו Termius, תומכות
   local port forwarding): מעבירים פורט מקומי 6080 אל פורט 6080 בשרת.
2. פותחים בדפדפן באייפון: `http://localhost:6080/vnc.html` — אמורים
   לראות desktop וירטואלי ריק.
3. ב-session SSH נוסף (או ברקע), מריצים על השרת:

```bash
DISPLAY=:99 python3 scripts/login_helper.py shufersal data/sessions/shufersal_storage_state.json
```

נפתח חלון דפדפן אמיתי בעמוד ההתחברות של שופרסל — **רואים אותו דרך
ה-noVNC באייפון** ומתחברים שם ידנית (כולל קוד אימות אם יש), ואז חוזרים
לטרמינל ה-SSH ומאשרים ב-Enter כדי לשמור את ה-session. מאותו רגע, כל
ריצה עתידית של האוטומציה משתמשת בקובץ הזה בלי לבקש מכם דבר, עד
שהשופרסל יפסול את ה-session (נדיר).

בסיום, כדאי לסגור את ה-desktop הזמני:

```bash
./scripts/stop_remote_desktop.sh
```

**לא מאומת עדיין** — נכתב והורץ syntax-check בלבד; לא נבדק בפועל מול
Contabo אמיתי (סוג הפצת הלינוקס, שמות חבילות ה-apt, גרסת Chromium
הנתמכת). זה בדיוק מה שצריך לבדוק בפעם הראשונה שמריצים את זה על השרת.

אם יש לכם בכל זאת גישה נוחה למחשב עם מסך רגיל, אפשר גם להריץ את
`scripts/login_helper.py` שם ישירות (בלי `setup_remote_desktop.sh`
ובלי `DISPLAY=:99`) ואז להעתיק את קובץ ה-session לשרת.

### הרצה

```bash
python -m grocery_bot.main
```

הבוט עולה ב-polling. בטלגרם:
- כל הודעת טקסט חופשית → נכנסת לתור האד-הוק למחזור הבא.
- `/start_order` → מריץ מחזור קנייה אמיתי (רשימת בסיס + כל האד-הוק
  שהצטבר), ממלא את העגלה האמיתית בשופרסל, ושולח סיכום. פריט עם כמה
  תוצאות חיפוש מתאימות נשאל בנפרד עם כפתורים — לא חוסם את שאר המחזור.
- `/list` → מציג את רשימת הבסיס הפעילה.

לטעינת רשימת הבסיס הראשונית (העתיקו/ערכו את `data/base_list.example.yaml`
לרשימה האמיתית שלכם קודם):

```bash
python -c "from grocery_bot.storage import Storage; from grocery_bot.config import Config; \
  Storage(Config.from_env().db_path).import_base_list_from_yaml('data/base_list.yaml')"
```

## הרצת הבדיקות

```bash
python -m unittest discover -s tests -v
```

הבדיקות מכסות רק לוגיקה טהורה (storage, אורקסטרטור) עם אדפטר מזויף —
בלי Playwright ובלי טלגרם, כדי שירוצו בכל מקום בלי תלות חיצונית.

## כוונון מתאם שופרסל (הכי חשוב לפני שימוש אמיתי)

`grocery_bot/adapters/shufersal.py` נכתב בלי גישה לחשבון אמיתי, אז ה-URL
וה-selectors (בראש הקובץ, כקבועים) הם ניחוש מושכל לפי מבנה טיפוסי של
אתר קמעונאות — לא מאומתים מול האתר בפועל. הדרך לכוון אותם:

1. מריצים עם `PLAYWRIGHT_HEADLESS=false` כדי לראות את הדפדפן בזמן אמת.
2. מריצים `/start_order` פעם אחת ועוקבים איפה זה נשבר (חיפוש? זיהוי
   כרטיס מוצר? כפתור "הוספה לסל"?).
3. מתקנים את הקבועים בראש `shufersal.py` (`SEARCH_URL_TEMPLATE`,
   `PRODUCT_CARD_SELECTOR`, `PRODUCT_NAME_SELECTOR`, `ADD_TO_CART_SELECTOR`)
   לפי מה שרואים ב-DevTools של הדפדפן.

לאחר כוונון חד-פעמי זה, ה-Phase 1 אמור לעבוד מקצה לקצה.

## מבנה הפרויקט

```
grocery_bot/
  config.py        # קונפיגורציה מ-env vars בלבד
  models.py        # מבני נתונים משותפים
  storage.py        # SQLite: רשימת בסיס, תור אד-הוק, עמימויות ממתינות
  orchestrator.py   # מיזוג רשימה+אד-הוק והרצת מחזור קנייה
  telegram_bot.py   # handlers של הבוט
  main.py           # entrypoint
  adapters/
    base.py          # ממשק StoreAdapter
    shufersal.py      # מימוש Playwright לשופרסל (Phase 1)
scripts/login_helper.py  # לכידת session חד-פעמית
data/base_list.example.yaml
tests/
```
