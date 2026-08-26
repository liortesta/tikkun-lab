/* A first-run walkthrough.
 *
 * The lab shows a lot at once and almost none of it is self-explanatory: a
 * receptor count, an eliciting dose, a provenance class. Someone opening this
 * for the first time needs to be told what each area is *for* before any of the
 * numbers mean anything.
 *
 * Spotlight rather than a wall of text: each step dims the page, cuts a hole
 * around one element and explains that element only. Shown once, then available
 * from the ? button in the top bar.
 */

const SEEN_KEY = 'tikkun-lab-tour-seen-v1';

const STEPS = [
  {
    target: null,
    title: 'ברוך הבא למעבדת תיקון',
    body: 'זו סימולציה של אלרגיה לחלב פרה. אתה מגדיר מטופל, נותן לו מנת חלב, '
      + 'ורואה בדיוק מה קורה בגוף שלו — מרמת הקולטן על התא ועד הסימפטום.\n\n'
      + 'כל מספר כאן מחושב ממשוואות עם מקור מדעי. שום מספר לא מגיע ממודל שפה.',
  },
  {
    target: '#chips',
    title: 'קודם כול — מי המטופל',
    body: 'ארבעה מטופלים מוכנים, מהרגיש ביותר עד מי שכבר החלים. או הזז את '
      + 'הסליידרים בעצמך.\n\nהמספר החשוב הוא IgE ספציפי לחלב — כמה נוגדנים שמזהים '
      + 'חלב יש לו. זה מה שקובע כמה קטנה המנה שתפיל אותו.',
  },
  {
    target: '#cell-canvas',
    title: 'תא הפיטום — כאן מתחילה התגובה',
    body: 'התא הזה הוא מה שמפעיל את האלרגיה. על פניו יש קולטנים; חלקם נושאים '
      + 'נוגדן שמזהה חלב (כחול).\n\nכשחלבון חלב מגשר בין שניים מהם — הקולטן נצבע '
      + 'זהוב. מספיקים כמאה גישורים כדי שהתא יתפוצץ וישחרר היסטמין (כתום).\n\n'
      + 'גרור כדי לסובב את התא.',
  },
  {
    target: '#dose',
    title: 'עכשיו תן לו חלב',
    body: 'הזז את המינון וראה הכול מגיב מיד — הקישורים על התא, ההיסטמין, הציון.\n\n'
      + 'הסליידר לוגריתמי: מפירור בלתי נראה ועד כוס מלאה. מתחתיו כתוב כמה חלב '
      + 'זה בפועל.',
  },
  {
    target: '#verdict',
    title: 'התוצאה',
    body: 'ציון 0 עד 10. מ-3 ומעלה זו תגובה — זו הנקודה שבה אתגר מזון קליני '
      + 'אמיתי נחשב חיובי.\n\nמתחת לזה תמצא את המדדים שמסבירים למה, כל אחד עם '
      + 'שורת הסבר משלו.',
  },
  {
    target: '#shift',
    title: 'הסף המעורר — המספר שהכול סובב סביבו',
    body: 'המנה הקטנה ביותר שגורמת לתגובה. זה מה שרופאים מודדים, וזה מה שטיפול '
      + 'מנסה להזיז כלפי מעלה.\n\nכשתדליק טיפול, תראה כאן בדיוק בכמה הוא הזיז אותו.',
  },
  {
    target: '#levers',
    title: 'חמישה טיפולים אמיתיים',
    body: 'כל אחד הוא מנגנון מתועד עם ציטוט: אימונותרפיה, אנטי-IgE, נוגדן חוסם, '
      + 'תיקון מחסום, מייצב תאים.\n\nסמן אחד או כמה וראה את הסף זז. אלה בדיוק '
      + 'הכלים שסוכן ה-AI רשאי לבחור מהם.',
  },
  {
    target: '#nav button[data-tab="agents"]',
    title: 'צבא הסוכנים',
    body: 'כאן קורה הדבר המעניין. חוקר ראשי מתכנן פרוטוקולים, המנוע מריץ אותם, '
      + 'שלושה מומחים בודקים, ומבקר מדרג.\n\nולבסוף המנוע מכייל את המינונים של '
      + 'הפרוטוקול המוביל — ומוודא שהוא בטוח כבר במנה הראשונה. את התוצאה אפשר '
      + 'לטעון בחזרה לשולחן העבודה.',
  },
  {
    target: '#nav button[data-tab="params"]',
    title: 'ומאיפה כל מספר',
    body: '33 פרמטרים, כל אחד עם יחידה, ציטוט וסיווג: נמדד בניסוי, הותאם לשחזר '
      + 'תוצאה קלינית, או הנחת מידול.\n\nזה מה שמפריד בין סימולציה שאפשר לסמוך '
      + 'עליה לבין הדגמה יפה.',
  },
  {
    target: null,
    title: 'זהו. אתה מוכן',
    body: 'התחל בלגרור את סליידר המינון וראה מה קורה.\n\nאפשר להחזיר את ההדרכה '
      + 'בכל רגע מכפתור ה-? למעלה.',
  },
];

export function tourWasSeen() {
  try {
    return localStorage.getItem(SEEN_KEY) === '1';
  } catch {
    return true;  // private browsing: do not nag on every load
  }
}

function markSeen() {
  try { localStorage.setItem(SEEN_KEY, '1'); } catch { /* nothing to do */ }
}

export function startTour({ onStep } = {}) {
  const existing = document.querySelector('.tour-root');
  if (existing) existing.remove();

  const root = document.createElement('div');
  root.className = 'tour-root';
  root.innerHTML =
    '<div class="tour-veil"></div>'
    + '<div class="tour-ring" hidden></div>'
    + '<div class="tour-card" role="dialog" aria-modal="true" aria-live="polite">'
    + '  <div class="tour-count"></div>'
    + '  <h3></h3><p></p>'
    + '  <div class="tour-actions">'
    + '    <button class="btn quiet" data-act="skip">דלג</button>'
    + '    <span style="flex:1"></span>'
    + '    <button class="btn quiet" data-act="back">הקודם</button>'
    + '    <button class="btn" data-act="next">הבא</button>'
    + '  </div>'
    + '</div>';
  document.body.appendChild(root);

  const veil = root.querySelector('.tour-veil');
  const ring = root.querySelector('.tour-ring');
  const card = root.querySelector('.tour-card');
  const title = card.querySelector('h3');
  const body = card.querySelector('p');
  const count = card.querySelector('.tour-count');
  const backBtn = card.querySelector('[data-act="back"]');
  const nextBtn = card.querySelector('[data-act="next"]');

  let index = 0;

  function close() {
    markSeen();
    document.removeEventListener('keydown', onKey);
    window.removeEventListener('resize', place);
    root.remove();
  }

  function place() {
    const step = STEPS[index];
    const target = step.target ? document.querySelector(step.target) : null;

    if (!target) {
      ring.hidden = true;
      card.style.insetInlineStart = '';
      card.style.top = '';
      card.classList.add('centred');
      return;
    }

    const box = target.getBoundingClientRect();
    // A target scrolled out of view cannot be pointed at.
    if (box.top < 60 || box.bottom > window.innerHeight - 60) {
      target.scrollIntoView({ block: 'center', behavior: 'smooth' });
      setTimeout(place, 320);
      return;
    }

    const pad = 8;
    ring.hidden = false;
    Object.assign(ring.style, {
      top: `${box.top - pad}px`, left: `${box.left - pad}px`,
      width: `${box.width + pad * 2}px`, height: `${box.height + pad * 2}px`,
    });

    card.classList.remove('centred');
    const cardBox = card.getBoundingClientRect();
    // Prefer below the target, flip above when there is no room.
    let top = box.bottom + 14;
    if (top + cardBox.height > window.innerHeight - 16) {
      top = Math.max(16, box.top - cardBox.height - 14);
    }
    let left = box.left + box.width / 2 - cardBox.width / 2;
    left = Math.max(16, Math.min(left, window.innerWidth - cardBox.width - 16));
    card.style.top = `${top}px`;
    card.style.left = `${left}px`;
    card.style.insetInlineStart = 'auto';
  }

  function show() {
    const step = STEPS[index];
    title.textContent = step.title;
    body.textContent = step.body;
    count.textContent = `${index + 1} מתוך ${STEPS.length}`;
    backBtn.disabled = index === 0;
    nextBtn.textContent = index === STEPS.length - 1 ? 'סיום' : 'הבא';
    onStep?.(step, index);
    requestAnimationFrame(place);
  }

  function go(delta) {
    const next = index + delta;
    if (next < 0) return;
    if (next >= STEPS.length) return close();
    index = next;
    show();
  }

  function onKey(event) {
    if (event.key === 'Escape') close();
    // Arrows are mirrored in a right-to-left page: left advances.
    else if (event.key === 'ArrowLeft' || event.key === 'Enter') go(1);
    else if (event.key === 'ArrowRight') go(-1);
  }

  card.addEventListener('click', event => {
    const act = event.target.dataset?.act;
    if (act === 'skip') close();
    else if (act === 'next') go(1);
    else if (act === 'back') go(-1);
  });
  veil.addEventListener('click', close);
  document.addEventListener('keydown', onKey);
  window.addEventListener('resize', place);

  show();
  return { close };
}
