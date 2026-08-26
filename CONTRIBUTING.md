# Contributing · תרומה לפרויקט

*[English](#english) · [עברית](#עברית)*

## English

Thank you for wanting to build on this. A few things are load-bearing.

### The one rule

**No language model may produce a number.**

Before writing anything, work out which side of that line your change falls on:

- The **engine** (`engine/`) computes values from equilibrium binding theory and ODEs.
- **Agents** (`agents/`) choose from a closed intervention vocabulary and interpret what came back.
- `agents/guard.py` flags any figure in agent prose that cannot be traced to engine output.

If a change would let a model emit a quantity, it is the wrong change. Add a
lever to `agents/protocol.py` instead — with its mechanism and a citation.

### Parameters need provenance

Never add a bare float to `engine/params_milk.py`. Every value carries a unit, a
citation and a provenance class:

| Class | Meaning |
|---|---|
| `MEASURED` | reported directly in the cited experiment |
| `DERIVED` | arithmetic on measured values |
| `CALIBRATED` | fitted to reproduce a published clinical endpoint |
| `ASSUMED` | a modelling choice with nothing behind it |

### Keep fitting and testing apart

`calibrate.py` fits parameters to published anchors. `validate.py` checks those
anchors **and** predictions the fit never saw. The prediction count in the
validation footer is this project's actual evidence.

Never fit a parameter to something `validate.py` lists as a `PREDICTION`. If you
genuinely need to, move that check into `ANCHORS` explicitly and say so in the
commit, so the prediction count drops visibly rather than silently.

### After changing the engine

```bash
python calibrate.py          # refit, then paste values into params_milk.py
python validate.py           # must stay 14/14
python web/fixture.py        # refresh reference values for the browser port
node web/build.mjs           # rebuild (runs verify + frontend-check first)
python -m pytest tests/ -q
```

The browser engine is a separate JavaScript port and is not trusted: change the
Python engine without regenerating the fixture and `verify.mjs` will happily
confirm the port still matches a stale reference while the published page serves
numbers that were never calibrated. `tests/test_web.py` closes that loop.

### Before opening a pull request

```bash
python -m pytest tests/ -q
python validate.py
node web/verify.mjs
node web/frontend-check.mjs
python scripts/audit_secrets.py
```

Never commit a credential. `scripts/audit_secrets.py` scans exactly what git
would publish and exits non-zero if it finds one; run it before every push.
Keys belong in `.env` (git-ignored) or the environment — see `.env.example`.

### Scope

Good contributions: a second disease behind the same engine and agent loop,
more measured parameters replacing assumed ones, additional validation
predictions, better mechanism visualisation, translations.

Please open an issue before large architectural changes.

---

## עברית

תודה שאתה רוצה לבנות על זה. כמה דברים הם קריטיים.

### הכלל היחיד

**שום מודל שפה לא רשאי לייצר מספר.**

לפני שאתה כותב משהו, קבע באיזה צד של הקו השינוי שלך נמצא:

- **המנוע** (`engine/`) מחשב ערכים משיווי משקל קישור וממערכות ODE.
- **הסוכנים** (`agents/`) בוחרים מאוצר מילים סגור של התערבויות ומפרשים את מה שחזר.
- `agents/guard.py` מסמן כל מספר בטקסט של סוכן שאינו ניתן לייחוס לפלט המנוע.

אם שינוי מאפשר למודל לפלוט כמות — זה השינוי הלא נכון. הוסף מנוף
ל-`agents/protocol.py` במקום, עם המנגנון והציטוט שלו.

### לכל פרמטר צריך מקור

לעולם אל תוסיף מספר חשוף ל-`engine/params_milk.py`. לכל ערך יש יחידה, ציטוט
וסיווג מקור: `MEASURED` (דווח ישירות בניסוי), `DERIVED` (חישוב על ערכים
מדודים), `CALIBRATED` (הותאם לשחזר תוצאה קלינית), `ASSUMED` (בחירת מידול).

### שמור על הפרדה בין התאמה לבדיקה

`calibrate.py` מתאים פרמטרים לעוגנים מפורסמים. `validate.py` בודק גם את
העוגנים האלה **וגם** תחזיות שההתאמה מעולם לא ראתה. מספר התחזיות בתחתית
הולידציה הוא העדות האמיתית של הפרויקט.

לעולם אל תתאים פרמטר למשהו ש-`validate.py` מסמן כ-`PREDICTION`. אם אתה באמת
חייב — העבר את הבדיקה ל-`ANCHORS` במפורש וציין זאת ב-commit, כדי שמספר
התחזיות ירד בגלוי ולא בשקט.

### לפני Pull Request

```bash
python -m pytest tests/ -q
python validate.py
node web/verify.mjs
node web/frontend-check.mjs
python scripts/audit_secrets.py
```

**לעולם אל תעלה מפתח.** `scripts/audit_secrets.py` סורק בדיוק את מה שgit היה
מפרסם ונכשל אם הוא מוצא אחד. מפתחות שייכים ל-`.env` (מוחרג מ-git) או לסביבה.

### תחומי תרומה

תרומות טובות: מחלה שנייה מאחורי אותו מנוע ואותה לולאת סוכנים, פרמטרים מדודים
שמחליפים מונחים, תחזיות ולידציה נוספות, ויזואליזציה טובה יותר של המנגנון, תרגומים.

אנא פתח issue לפני שינויים ארכיטקטוניים גדולים.
