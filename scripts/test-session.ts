/**
 * Session cookie tests. Run with:  npx tsx scripts/test-session.ts
 *
 * A forged cookie here means anyone can impersonate either of them, so these
 * cases matter more than their size suggests.
 */
process.env.AUTH_SECRET ||= "test-secret-that-is-at-least-32-chars-long";

import { createHmac } from "node:crypto";
import { signSession, verifySession } from "../src/lib/session";

let failures = 0;
function check(label: string, ok: boolean, detail?: unknown) {
  if (ok) console.log(`  ✓ ${label}`);
  else {
    failures++;
    console.log(`  ✗ ${label}`, detail ?? "");
  }
}

async function main() {
  const realSecret = process.env.AUTH_SECRET!;
  const token = await signSession("liran");

  check("round-trips the signed-in person", (await verifySession(token)) === "liran");
  check("rejects a missing cookie", (await verifySession(undefined)) === null);
  check("rejects garbage", (await verifySession("nonsense")) === null);

  // Flip a character in the signature.
  const [body, sig] = token.split(".");
  const tampered = `${body}.${sig.slice(0, -1)}${sig.at(-1) === "A" ? "B" : "A"}`;
  check("rejects a tampered signature", (await verifySession(tampered)) === null);

  // Swap the payload for a different person, keeping the original signature.
  const forgedBody = Buffer.from(
    JSON.stringify({ k: "ishay", exp: Math.floor(Date.now() / 1000) + 999 }),
  ).toString("base64url");
  check("rejects a swapped payload", (await verifySession(`${forgedBody}.${sig}`)) === null);

  // Correctly signed, but past its expiry.
  const expiredBody = Buffer.from(
    JSON.stringify({ k: "ishay", exp: Math.floor(Date.now() / 1000) - 10 }),
  ).toString("base64url");
  const expiredSig = createHmac("sha256", realSecret).update(expiredBody).digest("base64url");
  check(
    "rejects an expired session",
    (await verifySession(`${expiredBody}.${expiredSig}`)) === null,
  );

  // Signed with a different key entirely.
  process.env.AUTH_SECRET = "a-completely-different-secret-key-32chars";
  check("rejects a token signed with another key", (await verifySession(token)) === null);
  process.env.AUTH_SECRET = realSecret;

  console.log(failures === 0 ? "\n✅ All session checks passed.\n" : `\n❌ ${failures} failed.\n`);
  process.exit(failures === 0 ? 0 : 1);
}

main();
