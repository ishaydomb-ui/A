import { all, one, run, json, logActivity } from "../db";
import { resolveProduct } from "./prices";

/**
 * Turning a meal plan into a priced shopping list.
 *
 * Three sources feed a list, in this order:
 *   1. ingredients for every planned meal in the window
 *   2. staples that have run out or are about to expire
 *   3. anything explicitly asked for
 * Then we subtract what's already in the pantry, and price the rest.
 */

export interface BuiltList {
  listId: number;
  name: string;
  chain: string;
  items: Array<{
    name: string;
    qty: number;
    unit: string | null;
    source: string;
    matched: string | null;
    price: number | null;
  }>;
  estimatedTotal: number;
  unmatched: string[];
}

export function buildGroceryList(
  input: { from: string; to: string; chain?: string; extra?: string[] },
  actor: string,
): BuiltList {
  const chain = input.chain ?? process.env.GROCERY_DEFAULT_CHAIN ?? "shufersal";

  const wanted = new Map<string, { qty: number; unit: string | null; source: string }>();
  const add = (name: string, qty: number, unit: string | null, source: string) => {
    const key = name.trim();
    if (!key) return;
    const existing = wanted.get(key);
    if (existing) existing.qty += qty;
    else wanted.set(key, { qty, unit, source });
  };

  // 1. meal plan ingredients
  const meals = all<{ title: string; ingredients: string | null }>(
    `SELECT mp.title, r.ingredients
     FROM meal_plan mp LEFT JOIN recipes r ON r.id = mp.recipe_id
     WHERE mp.plan_date >= ? AND mp.plan_date <= ?`,
    [input.from, input.to],
  );
  for (const meal of meals) {
    const ingredients = json<Array<{ name: string; qty?: number; unit?: string }>>(
      meal.ingredients,
      [],
    );
    for (const ing of ingredients) {
      add(ing.name, ing.qty ?? 1, ing.unit ?? null, "meal_plan");
    }
  }

  // 2. staples that are gone or expiring
  const staples = all<{ name: string; qty: number | null; unit: string | null }>(
    `SELECT name, qty, unit FROM pantry_items
     WHERE staple = 1 AND (
       qty IS NULL OR qty <= 0
       OR (expires_at IS NOT NULL AND date(expires_at) <= date('now', '+3 days'))
     )`,
  );
  for (const s of staples) add(s.name, 1, s.unit, "staple");

  // 3. explicit extras
  for (const item of input.extra ?? []) add(item, 1, null, "manual");

  // subtract what we already have in usable quantity
  const stocked = new Set(
    all<{ name: string }>(
      `SELECT name FROM pantry_items WHERE qty > 0 AND staple = 0
       AND (expires_at IS NULL OR date(expires_at) > date('now'))`,
    ).map((r) => r.name.trim().toLowerCase()),
  );

  const res = run(
    `INSERT INTO grocery_lists (name, chain, status) VALUES (?, ?, 'open')`,
    [`Groceries ${input.from} → ${input.to}`, chain],
  );
  const listId = res.lastInsertRowid as number;

  const items: BuiltList["items"] = [];
  const unmatched: string[] = [];
  let total = 0;

  for (const [name, meta] of wanted) {
    if (stocked.has(name.toLowerCase()) && meta.source !== "manual") continue;

    const match = resolveProduct(name, chain);
    const price = match?.price ?? null;
    if (!match) unmatched.push(name);
    if (price) total += price * meta.qty;

    run(
      `INSERT INTO grocery_items
        (list_id, name, qty, unit, item_code, matched_name, est_price, source)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
      [
        listId,
        name,
        meta.qty,
        meta.unit,
        match?.item_code ?? null,
        match?.name ?? null,
        price,
        meta.source,
      ],
    );
    items.push({
      name,
      qty: meta.qty,
      unit: meta.unit,
      source: meta.source,
      matched: match?.name ?? null,
      price,
    });
  }

  run(`UPDATE grocery_lists SET est_total = ? WHERE id = ?`, [total, listId]);
  logActivity({
    actor,
    action: "built_grocery_list",
    entityType: "grocery_list",
    entityId: listId,
    summary: `Built list of ${items.length} items (~${total.toFixed(0)} ILS) for ${chain}`,
    detail: { unmatched },
  });

  return {
    listId,
    name: `Groceries ${input.from} → ${input.to}`,
    chain,
    items,
    estimatedTotal: Math.round(total * 100) / 100,
    unmatched,
  };
}

export function getList(id: number) {
  const list = one(`SELECT * FROM grocery_lists WHERE id = ?`, [id]);
  if (!list) return undefined;
  return {
    ...list,
    items: all(`SELECT * FROM grocery_items WHERE list_id = ? ORDER BY category, name`, [id]),
  };
}

export function openLists() {
  return all(
    `SELECT gl.*, (SELECT COUNT(*) FROM grocery_items gi WHERE gi.list_id = gl.id) AS item_count
     FROM grocery_lists gl WHERE gl.status != 'done' ORDER BY gl.created_at DESC`,
  );
}
