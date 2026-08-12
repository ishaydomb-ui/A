import type { StoreAdapter } from "./types";
import { shufersalAdapter } from "./shufersal";
import { tivtaamAdapter } from "./tivtaam";

const adapters: Record<string, StoreAdapter> = {
  shufersal: shufersalAdapter,
  tivtaam: tivtaamAdapter,
};

export function getAdapter(chain: string): StoreAdapter {
  const adapter = adapters[chain];
  if (!adapter) throw new Error(`No adapter for chain "${chain}"`);
  return adapter;
}

export function listAdapters() {
  return Object.values(adapters).map((a) => ({
    key: a.key,
    label: a.label,
    maturity: a.maturity,
  }));
}

export type { StoreAdapter };
