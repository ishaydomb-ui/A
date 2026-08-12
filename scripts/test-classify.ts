/**
 * Classifier tests.
 *
 * Every case below is a real title from the household calendar. This is the
 * highest-leverage test in the project: if classification is wrong, every
 * schedule answer built on top of it is confidently wrong.
 *
 * Run with:  npx tsx scripts/test-classify.ts
 */
import { classifyEvent, type PersonRef } from "../src/lib/google/classify";

const PEOPLE: PersonRef[] = [
  { key: "ishay", name: "Ishay", nameHe: "ישי", email: "ishaydomb@gmail.com", role: "adult" },
  { key: "liran", name: "Liran", nameHe: "לירן", email: "lirikor@gmail.com", role: "adult" },
  { key: "yanai", name: "Yanai", nameHe: "ינאי", email: null, role: "child" },
  { key: "berry", name: "Berry", nameHe: "ברי", email: null, role: "child" },
];

const ISHAY = "ishaydomb@gmail.com";
const LIRAN = "lirikor@gmail.com";

interface Case {
  title: string;
  creator?: string;
  expect: { kind: string | null; subject?: string | null; owner?: string | null };
}

const CASES: Case[] = [
  // Kids' recurring activities
  { title: "חוג ברי", creator: ISHAY, expect: { kind: "class", subject: "berry", owner: "ishay" } },
  { title: "חוג ינאי", creator: ISHAY, expect: { kind: "class", subject: "yanai", owner: "ishay" } },

  // Pickups - the case that "which days am I picking up the kids" depends on
  {
    title: "לאסוף את ינאי וברי ב-14 מאטי",
    creator: ISHAY,
    expect: { kind: "pickup", subject: null, owner: "ishay" },
  },
  {
    title: "לאסוף את ברי",
    creator: LIRAN,
    expect: { kind: "pickup", subject: "berry", owner: "liran" },
  },

  // On-call: Liran named in a shift event created by Ishay is still Liran's shift
  {
    title: "לירן בתורנות",
    creator: ISHAY,
    expect: { kind: "oncall", subject: "liran", owner: "liran" },
  },
  { title: "מילואים", creator: ISHAY, expect: { kind: "reserve", subject: "ishay", owner: "ishay" } },

  // Appointments
  {
    title: "תור רופא שיניים ינאי וברי 18:20",
    creator: ISHAY,
    expect: { kind: "appointment", subject: null, owner: "ishay" },
  },
  { title: "תור אחות", creator: ISHAY, expect: { kind: "appointment", owner: "ishay" } },
  { title: "בדיקות דם", creator: ISHAY, expect: { kind: "appointment", owner: "ishay" } },
  {
    title: "12:00 פגישה עם קבוצת תמורה - סוכנות לביטוח",
    creator: ISHAY,
    expect: { kind: "appointment" },
  },
  { title: "סקירה ראשונה", creator: LIRAN, expect: { kind: "appointment", owner: "liran" } },

  // Travel
  { title: "Flight IZ51: Tel Aviv → Corfu", creator: ISHAY, expect: { kind: "travel" } },
  { title: "טיסת חזור מקורפו לתל אביב – ארקיע IZ54", creator: ISHAY, expect: { kind: "travel" } },
  { title: "סופש בנוקדים", creator: LIRAN, expect: { kind: "travel" } },

  // Home services
  { title: "טכנאי מזגנים", creator: ISHAY, expect: { kind: "home" } },

  // Occasions and outings
  { title: "יום הולדת לברי", creator: ISHAY, expect: { kind: "occasion", subject: "berry" } },
  { title: "אזכרה לתלמה", creator: ISHAY, expect: { kind: "occasion" } },
  { title: "הצגה פיטר פן", creator: ISHAY, expect: { kind: "outing" } },
  { title: "מופע Glow", creator: ISHAY, expect: { kind: "outing" } },

  // Things that should NOT be force-labelled
  { title: "Guy OOO", creator: ISHAY, expect: { kind: null } },
  { title: "עם הילדים", creator: ISHAY, expect: { kind: null } },
];

let failures = 0;

for (const testCase of CASES) {
  const result = classifyEvent(
    { title: testCase.title, creatorEmail: testCase.creator },
    PEOPLE,
  );

  const problems: string[] = [];
  if (result.kind !== testCase.expect.kind) {
    problems.push(`kind: expected ${testCase.expect.kind}, got ${result.kind}`);
  }
  if ("subject" in testCase.expect && result.subjectKey !== testCase.expect.subject) {
    problems.push(`subject: expected ${testCase.expect.subject}, got ${result.subjectKey}`);
  }
  if ("owner" in testCase.expect && result.ownerKey !== testCase.expect.owner) {
    problems.push(`owner: expected ${testCase.expect.owner}, got ${result.ownerKey}`);
  }

  if (problems.length) {
    failures++;
    console.log(`  ✗ "${testCase.title}"`);
    for (const p of problems) console.log(`      ${p}`);
  } else {
    console.log(`  ✓ "${testCase.title}" → ${result.kind ?? "unlabelled"}`);
  }
}

console.log(
  failures === 0
    ? `\n✅ All ${CASES.length} classification cases passed.\n`
    : `\n❌ ${failures} of ${CASES.length} cases failed.\n`,
);
process.exit(failures === 0 ? 0 : 1);
