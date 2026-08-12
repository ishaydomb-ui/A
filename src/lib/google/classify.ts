/**
 * Turning raw calendar entries into answerable facts.
 *
 * A Google event is just a title and a time. "Which days am I picking up the
 * kids" is only answerable if something has already decided that
 * "לאסוף את ינאי וברי" is a pickup owned by whoever created it. That decision
 * happens once, here, at sync time - so the question itself stays a cheap query.
 *
 * Rules are deliberately explicit rather than model-inferred: classification
 * runs over every event on every sync, it must be deterministic, and a wrong
 * label here silently corrupts every downstream answer. Patterns come from the
 * household's own calendar vocabulary, Hebrew first.
 */

export type EventKind =
  | "pickup"
  | "dropoff"
  | "class"
  | "appointment"
  | "oncall"
  | "reserve"
  | "travel"
  | "outing"
  | "occasion"
  | "home"
  | null;

export interface RawEvent {
  title: string;
  description?: string | null;
  location?: string | null;
  allDay?: boolean;
  creatorEmail?: string | null;
  organizerEmail?: string | null;
}

export interface Classification {
  kind: EventKind;
  /** Whose event it is - a child's class, a parent's shift. */
  subjectKey: string | null;
  /** Who is responsible for making it happen. */
  ownerKey: string | null;
}

export interface PersonRef {
  key: string;
  name: string;
  nameHe?: string | null;
  email?: string | null;
  role: string;
}

/**
 * Hebrew-safe whole-word matcher.
 *
 * JavaScript's \b is defined over ASCII word characters only, so /\bחוג\b/
 * never matches — the boundary assertion fails on both sides because Hebrew
 * letters aren't \w. Match against real delimiters instead, and allow the
 * single-letter prefixes Hebrew glues onto words (ל, ב, ה, מ, ש, ו, כ) so
 * "לחוג" still reads as "חוג".
 */
const HE_EDGE = `[\\s\\-־,.:;"'()\\[\\]/]`;
function he(word: string): RegExp {
  return new RegExp(`(^|${HE_EDGE})[בלמהשוכ]?${word}(${HE_EDGE}|$)`);
}

/** Ordered: the first matching rule wins, so put specific before general. */
const RULES: Array<{ kind: Exclude<EventKind, null>; patterns: RegExp[] }> = [
  {
    kind: "pickup",
    patterns: [/לאסוף/, /איסוף/, /לקחת מ/, /\bpick[\s-]?up\b/i, /\bcollect\b/i],
  },
  {
    kind: "dropoff",
    patterns: [/להביא ל/, /הסעה ל/, /\bdrop[\s-]?off\b/i],
  },
  {
    kind: "oncall",
    patterns: [/תורנות/, /במשמרת/, /\bon[\s-]?call\b/i, /\bshift\b/i],
  },
  {
    kind: "reserve",
    patterns: [/מילואים/, /\breserve duty\b/i],
  },
  {
    kind: "class",
    patterns: [he("חוג"), /חוגים/, /צהרון/, /\bclass\b/i, /\blesson\b/i, /\btraining\b/i],
  },
  {
    kind: "appointment",
    patterns: [
      he("תור"),
      /רופא/,
      /שיניים/,
      /בדיקות?\s/,
      /מרפאה/,
      /פגישה/,
      /סקירה/,
      /\bappointment\b/i,
      /\bdoctor\b/i,
      /\bdentist\b/i,
      /\bmeeting\b/i,
    ],
  },
  {
    kind: "travel",
    patterns: [
      he("טיסה"),
      /טיסת/,
      /\bflight\b/i,
      /נחיתה/,
      /שדה התעופה/,
      /\bairport\b/i,
      /סופ"?ש ב/,
      /סופש ב/,
      /חופשה/,
      /\bvacation\b/i,
      /\btrip\b/i,
    ],
  },
  {
    kind: "home",
    patterns: [/טכנאי/, /שרברב/, /חשמלאי/, /תיקון/, /\bplumber\b/i, /\btechnician\b/i, /\brepair\b/i],
  },
  {
    kind: "occasion",
    patterns: [/יום הולדת/, /אזכרה/, /חתונה/, /\bbirthday\b/i, /\bwedding\b/i, /\bmemorial\b/i],
  },
  {
    kind: "outing",
    patterns: [/הצגה/, /מופע/, /קונצרט/, /סרט/, /\bshow\b/i, /\bconcert\b/i, /\bmovie\b/i],
  },
];

export function classifyEvent(event: RawEvent, people: PersonRef[]): Classification {
  const haystack = [event.title, event.description ?? "", event.location ?? ""].join(" ");

  let kind: EventKind = null;
  for (const rule of RULES) {
    if (rule.patterns.some((p) => p.test(haystack))) {
      kind = rule.kind;
      break;
    }
  }

  const mentioned = peopleMentioned(haystack, people);
  const children = mentioned.filter((p) => p.role === "child");
  const adults = mentioned.filter((p) => p.role === "adult");

  // Subject: whose event is this really about?
  let subjectKey: string | null = null;
  if (kind === "oncall" || kind === "reserve") {
    // "לירן בתורנות" - the named adult is the subject. If nobody is named the
    // creator is on duty, since people log their own shifts.
    subjectKey = adults[0]?.key ?? creatorKey(event, people);
  } else if (children.length === 1) {
    subjectKey = children[0].key;
  } else if (children.length > 1) {
    // Both kids named ("ינאי וברי") - no single subject, and that is correct:
    // the event belongs to the household, not to one child.
    subjectKey = null;
  } else if (adults.length === 1) {
    subjectKey = adults[0].key;
  }

  // Owner: who has to actually do it?
  let ownerKey: string | null = null;
  if (kind === "pickup" || kind === "dropoff" || kind === "class" || kind === "appointment") {
    // Whoever put it in the calendar is doing the run, unless an adult other
    // than the creator is explicitly named in the title.
    const namedAdult = adults[0]?.key;
    const creator = creatorKey(event, people);
    ownerKey = namedAdult && namedAdult !== creator ? namedAdult : (creator ?? namedAdult ?? null);
  } else if (kind === "oncall" || kind === "reserve") {
    ownerKey = subjectKey;
  } else {
    ownerKey = creatorKey(event, people);
  }

  return { kind, subjectKey, ownerKey };
}

function peopleMentioned(text: string, people: PersonRef[]): PersonRef[] {
  const found: PersonRef[] = [];
  for (const person of people) {
    const names = [person.name, person.nameHe].filter(Boolean) as string[];
    if (names.some((n) => matchesName(text, n))) found.push(person);
  }
  return found;
}

/**
 * Hebrew has no case and glues prefixes onto names ("לברי" = "to Berry"), so a
 * plain word-boundary match misses real mentions. Latin names still get proper
 * boundaries to avoid matching inside longer words.
 */
function matchesName(text: string, name: string): boolean {
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  if (/[֐-׿]/.test(name)) return he(escaped).test(text);
  return new RegExp(`\\b${escaped}\\b`, "i").test(text);
}

function creatorKey(event: RawEvent, people: PersonRef[]): string | null {
  const email = (event.creatorEmail ?? event.organizerEmail ?? "").toLowerCase();
  if (!email) return null;
  return people.find((p) => p.email?.toLowerCase() === email)?.key ?? null;
}
