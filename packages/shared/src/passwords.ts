/**
 * The minimum password length, in one place.
 *
 * The client refuses a short password before it is sent and the server
 * refuses it again on arrival. Those two rules used to be separate constants
 * in separate packages, which is a disagreement waiting to happen: raise one
 * and the other silently rejects what the form just accepted, and the person
 * sees a password the page called fine come back as an error.
 *
 * Eight is a deliberate relaxation from twelve, chosen for what this system
 * actually holds: published drug reference text and a list of who may read
 * it. No patient-identifiable information is entered anywhere in it. Against
 * online guessing the real defences are elsewhere and unchanged — attempts
 * are rate-limited per account and per address, an account locks itself after
 * repeated failures, and every attempt is recorded. What eight characters
 * does concede is resistance to offline cracking if the password hashes
 * themselves ever leak; Argon2id makes that expensive rather than impossible.
 */
export const MIN_PASSWORD_LENGTH = 8;
