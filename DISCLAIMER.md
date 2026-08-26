# Disclaimer · הבהרה

## Not medical software

This project is a research and teaching simulation. It is **not a medical
device**, it has **not been clinically validated**, and it must **not** be used
to make or inform any decision about the care of a real person.

Specifically:

- **The eliciting dose is simulated, not measured.** A real patient may differ
  by an order of magnitude. Nothing here can tell you what dose is safe for
  anyone.
- **Eleven of the model's 33 parameters were fitted, not observed.** They were
  tuned so the model reproduces published clinical endpoints. Run
  `python cli.py audit`, or open the parameters tab in the app, to see the
  provenance class and citation of every single value.
- **The model is deliberately incomplete.** It has no mediators besides
  histamine — no tryptase, no PAF, no leukotrienes. It does not separate casein
  from whey. It does not simulate dose errors, missed doses, dose escalation,
  exercise, illness, or any other co-factor that shifts real thresholds.
- **Oral immunotherapy is dangerous outside supervised care.** The simulator
  will happily show you a protocol. Real oral immunotherapy is performed by
  allergists with emergency equipment present, and this model does not simulate
  the updosing that makes it survivable.

If you have or suspect a food allergy, talk to an allergist.

---

## זו לא תוכנה רפואית

הפרויקט הזה הוא סימולציה למחקר ולהוראה. הוא **אינו מכשיר רפואי**, הוא **לא עבר
ולידציה קלינית**, ו**אין להשתמש בו** כדי לקבל או לבסס שום החלטה על טיפול באדם
אמיתי.

בפרט:

- **הסף המעורר מסומלץ, לא נמדד.** מטופל אמיתי עלול להיות שונה בסדר גודל. שום
  דבר כאן לא יכול לומר לך איזה מינון בטוח עבור מישהו.
- **11 מתוך 33 הפרמטרים של המודל הותאמו, לא נמדדו.** הם כוילו כדי שהמודל ישחזר
  תוצאות קליניות מפורסמות. הרץ `python cli.py audit`, או פתח את לשונית
  הפרמטרים באפליקציה, כדי לראות את סיווג המקור והציטוט של כל ערך.
- **המודל חלקי בכוונה.** אין בו מתווכים מלבד היסטמין — לא טריפטאז, לא PAF, לא
  לויקוטריאנים. הוא לא מפריד בין קזאין למי גבינה. הוא לא מדמה טעויות מינון,
  מנות שהוחמצו, העלאת מינון הדרגתית, מאמץ, מחלה, או כל קו־פקטור אחר שמזיז ספים
  אמיתיים.
- **אימונותרפיה פומית מסוכנת מחוץ לפיקוח רפואי.** הסימולטור ישמח להראות לך
  פרוטוקול. אימונותרפיה אמיתית מבוצעת על ידי אלרגולוגים עם ציוד חירום זמין,
  והמודל הזה לא מדמה את העלאת המינון ההדרגתית שהופכת אותה לניתנת לשרידה.

אם יש לך או אתה חושד שיש לך אלרגיה למזון — דבר עם אלרגולוג.
