/**
 * Reading order and shipping emails.
 *
 * Same reasoning as calendar classification: this runs over every inbound
 * message, it must be deterministic and cheap, and a wrong call quietly
 * corrupts the "what's in transit" snapshot. So known vendors are matched by
 * explicit rules; anything unrecognised returns null and falls through to the
 * agent rather than being guessed at here.
 *
 * The negative cases matter as much as the positive ones. An airline booking
 * confirmation and a credit-card statement both look like "order" emails and
 * neither is a parcel.
 */

export type DeliveryStatus =
  | "ordered"
  | "shipped"
  | "in_transit"
  | "ready_for_pickup"
  | "delivered"
  | "cancelled";

export interface InboundEmail {
  from: string;
  subject: string;
  snippet?: string;
}

export interface ParsedDelivery {
  vendor: string;
  orderRef: string | null;
  /**
   * null means "this concerns an order but announces no new state" — an
   * "update on your order" email must not drag a delivered parcel backwards.
   */
  status: DeliveryStatus | null;
  description: string;
  confidence: "high" | "low";
}

/** How far along each state is. A later state never regresses to an earlier one. */
export const STATUS_RANK: Record<DeliveryStatus, number> = {
  cancelled: -1,
  ordered: 0,
  shipped: 1,
  in_transit: 2,
  ready_for_pickup: 3,
  delivered: 4,
};

/**
 * Senders that send order-shaped mail that is never a parcel. Checked first,
 * because "סיכום הזמנה ארקיע 13710917" is a flight, and a credit-card
 * statement is not a delivery no matter how many order words it contains.
 */
const NOT_DELIVERIES = [
  /arkia\.co\.il$/i,
  /elal\.co\.il$/i,
  /booking\.com$/i,
  /airbnb\./i,
  /cinema-city\.co\.il$/i,
  /\bcal(mail)?@/i,
  /icc\.co\.il$/i,
  /max-finance\.co\.il$/i,
  /leumi-card/i,
  /iec\.co\.il$/i,
  /bezeq/i,
  /btl\.gov\.il$/i,
  /education\.gov\.il$/i,
  /tel-aviv\.gov\.il$/i,
  /fnx\.co\.il$/i,
  /harel-group/i,
  /altshul/i,
  /linkedin\.com$/i,
  /glovoapp\.com$/i,
  /pizza\.dominos/i,
  /\bebay@ebay\.com$/i, // saved-search alerts, not orders
];

/** Known retailers and couriers, with the label to show. */
const VENDORS: Array<{ match: RegExp; vendor: string; carrier?: string }> = [
  { match: /iherb\.com$/i, vendor: "iHerb" },
  { match: /nextdirect\.com$/i, vendor: "Next" },
  { match: /e-vrit\.co\.il$/i, vendor: "עברית" },
  { match: /amazon\.(com|co\.uk|de)$/i, vendor: "Amazon" },
  { match: /aliexpress/i, vendor: "AliExpress" },
  { match: /asos\./i, vendor: "ASOS" },
  { match: /shein\./i, vendor: "SHEIN" },
  { match: /terminalx/i, vendor: "TerminalX" },
  { match: /ksp\.co\.il$/i, vendor: "KSP" },
  { match: /ivory\.co\.il$/i, vendor: "Ivory" },
  { match: /zara\./i, vendor: "Zara" },
  { match: /israelpost\.co\.il$|doar\.co\.il$/i, vendor: "דואר ישראל", carrier: "Israel Post" },
  { match: /hfd\.co\.il$/i, vendor: "HFD", carrier: "HFD" },
  { match: /baldarshlichim|cheetah|getpackage/i, vendor: "שליחויות", carrier: "courier" },
  { match: /dhl\./i, vendor: "DHL", carrier: "DHL" },
  { match: /ups\.com$/i, vendor: "UPS", carrier: "UPS" },
  { match: /fedex\./i, vendor: "FedEx", carrier: "FedEx" },
];

/**
 * Status keywords, most-advanced first so the strongest signal in a message
 * wins. Hebrew and English together — this inbox mixes them freely.
 */
const STATUS_RULES: Array<{ status: DeliveryStatus; patterns: RegExp[] }> = [
  {
    status: "cancelled",
    patterns: [/\bcancell?ed\b/i, /\brefunded\b/i, /בוטל/, /ביטול הזמנה/],
  },
  {
    status: "delivered",
    patterns: [
      /\bdelivered\b/i,
      /has been delivered/i,
      /נמסר/,
      /נמסרה/,
      /הגיע ליעד/,
      /המשלוח הגיע/,
    ],
  },
  {
    status: "ready_for_pickup",
    patterns: [
      /ready for (pick[\s-]?up|collection)/i,
      /available for pick[\s-]?up/i,
      /awaiting collection/i,
      /ממתין לאיסוף/,
      /מוכן לאיסוף/,
      /הגיע לנקודת האיסוף/,
      /לאיסוף בסניף/,
    ],
  },
  {
    status: "in_transit",
    patterns: [
      /out for delivery/i,
      /arriving today/i,
      /on its way/i,
      /יצא למשלוח/,
      /בדרך אליך/,
      /בהפצה/,
    ],
  },
  {
    status: "shipped",
    patterns: [
      /\bshipped\b/i,
      /has shipped/i,
      /\bdispatched\b/i,
      /on the way/i,
      /tracking number/i,
      /נשלח/,
      /נשלחה/,
      /יצאה מהמחסן/,
      /מספר מעקב/,
    ],
  },
  {
    status: "ordered",
    patterns: [
      /order (confirmation|received|placed)/i,
      /thank you for your order/i,
      /we('| ha)ve received your order/i,
      /אישור הזמנה/,
      /ההזמנה שלך התקבלה/,
      /הזמנתך נקלטה/,
      /תודה על הזמנתך/,
    ],
  },
];

/** Order references: "#554558877", "מספר הזמנה: 11519925", "order 13710917". */
const REF_PATTERNS = [
  /#\s*([A-Z0-9][A-Z0-9-]{4,})/i,
  /(?:order|invoice)\s*(?:no\.?|number|id)?\s*[:#]?\s*([A-Z0-9][A-Z0-9-]{4,})/i,
  /(?:מספר\s*)?הזמנה\s*(?:מספר)?\s*[:#]?\s*([0-9][0-9-]{4,})/,
  /הזמנתך\s*(?:מספר)?\s*[:#]?\s*([0-9][0-9-]{4,})/,
];

export function parseDeliveryEmail(email: InboundEmail): ParsedDelivery | null {
  const from = email.from.toLowerCase();
  if (NOT_DELIVERIES.some((p) => p.test(from))) return null;

  const haystack = `${email.subject} ${email.snippet ?? ""}`;

  const known = VENDORS.find((v) => v.match.test(from));

  // Subject first, body only as a fallback. The subject is the vendor's own
  // one-line summary of why they sent the message; the body is chatter around
  // it. Without this ordering, "your order was received and will soon be on its
  // way" reads as in-transit when it plainly means just-ordered.
  const status = matchStatus(email.subject) ?? matchStatus(haystack);

  // Mentions an order without announcing a state — keep the row, change nothing.
  const mentionsOrder = /\border\b/i.test(haystack) || /הזמנ/.test(haystack);

  if (!known && !status) return null;
  if (!known && status && !mentionsOrder) return null;

  const vendor = known?.vendor ?? vendorFromSender(from);
  if (!vendor) return null;

  return {
    vendor,
    orderRef: extractRef(haystack),
    status,
    description: email.subject.trim().slice(0, 200),
    // High confidence only for a known retailer with an explicit state.
    confidence: known && status ? "high" : "low",
  };
}

function matchStatus(text: string): DeliveryStatus | null {
  for (const rule of STATUS_RULES) {
    if (rule.patterns.some((p) => p.test(text))) return rule.status;
  }
  return null;
}

function extractRef(text: string): string | null {
  for (const pattern of REF_PATTERNS) {
    const match = text.match(pattern);
    if (match?.[1]) {
      // "554558877-0" and "554558877" are the same order at different stages.
      return match[1].split("-")[0];
    }
  }
  return null;
}

/** Fall back to the sending domain so an unknown shop is still identifiable. */
function vendorFromSender(from: string): string | null {
  const domain = from.split("@")[1]?.split(">")[0];
  if (!domain) return null;
  const parts = domain.split(".").filter((p) => !["com", "co", "il", "net", "www"].includes(p));
  const name = parts[parts.length - 1] ?? parts[0];
  if (!name || name.length < 3) return null;
  return name.charAt(0).toUpperCase() + name.slice(1);
}
