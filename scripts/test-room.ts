/**
 * The shared room. Run with:  npx tsx scripts/test-room.ts
 *
 * The behaviour worth testing isn't the plumbing, it's the restraint: an
 * assistant that answers every message turns a conversation between two people
 * into a conversation refereed by a bot. These cases pin down when it speaks.
 */
// Assigned unconditionally - this file deletes the database it points at.
process.env.DATABASE_PATH = "/tmp/beitenu-test-room.sqlite";

import fs from "node:fs";
for (const suffix of ["", "-wal", "-shm"]) {
  fs.rmSync(`${process.env.DATABASE_PATH}${suffix}`, { force: true });
}

import { db, run, all } from "../src/lib/db";
import { shouldRespond, getRoom, roomMessages, postToRoom } from "../src/lib/room";

db();
run(`INSERT OR IGNORE INTO people (key, name, role, color) VALUES ('ishay','Ishay','adult','#2563eb')`);
run(`INSERT OR IGNORE INTO people (key, name, role, color) VALUES ('liran','Liran','adult','#db2777')`);

let failures = 0;
function check(label: string, ok: boolean, detail?: unknown) {
  if (ok) console.log(`  ✓ ${label}`);
  else {
    failures++;
    console.log(`  ✗ ${label}`, detail ?? "");
  }
}

async function main() {
  console.log("\n— The assistant stays out of their conversation —");

  const silent = [
    "I think Saturday works better for the party",
    "yeah but your mum is away that weekend",
    "how many kids are we inviting?",
    "do you want to do it at the park or here?",
    "לא בטוח שכדאי ביום שבת",
    "כמה ילדים בערך?",
    "ok",
  ];
  for (const text of silent) {
    check(`silent on: "${text.slice(0, 44)}"`, shouldRespond(text) === false);
  }

  console.log("\n— …and answers when actually addressed —");

  const spoken: Array<[string, string]> = [
    ["@ai what's free on Saturday?", "@ mention"],
    ["assistant, check the calendar", "named"],
    ["add balloons to the party list", "instruction"],
    ["remind me to order the cake", "instruction"],
    ["can you check if Liran is on shift?", "polite request"],
    ["תוסיף בלונים לרשימה", "Hebrew instruction"],
    ["תזכיר לי להזמין עוגה", "Hebrew instruction"],
    ["אפשר לבדוק מתי לירן בתורנות?", "Hebrew request"],
  ];
  for (const [text, why] of spoken) {
    check(`responds to ${why}: "${text.slice(0, 36)}"`, shouldRespond(text) === true);
  }

  console.log("\n— Posting —");

  const roomId = getRoom();
  check("the room is created once", getRoom() === roomId);

  await postToRoom({ text: "I think Saturday works better", actor: "ishay" });
  await postToRoom({ text: "agreed, morning though", actor: "liran" });

  const msgs = roomMessages();
  check("both people's messages are in one thread", msgs.length === 2, msgs.length);
  check("each is attributed", msgs[0].speaker === "Ishay" && msgs[1].speaker === "Liran",
    msgs.map((m) => m.speaker));
  check(
    "the assistant did not chime in",
    msgs.every((m) => m.role === "user"),
  );

  const since = roomMessages(msgs[0].id);
  check("polling returns only what is new", since.length === 1 && since[0].speaker === "Liran");

  console.log("\n— The assistant is silent but not asleep —");
  const stored = all<{ n: number }>(
    `SELECT COUNT(*) AS n FROM messages WHERE conversation_id = ?`,
    [roomId],
  )[0].n;
  check("unaddressed messages are still recorded for context", stored === 2, stored);

  console.log(
    failures === 0 ? `\n✅ All room checks passed.\n` : `\n❌ ${failures} check(s) failed.\n`,
  );
  process.exit(failures === 0 ? 0 : 1);
}

main();
