import { useEffect, useId, useState } from 'react';
import { useI18n } from '../i18n.ts';
import type { Facets } from '../lib/types.ts';

export type FilterKey = 'therapeuticGroup' | 'drugClass' | 'drugFamily' | 'formulation';
export type FilterState = Record<FilterKey, string[]>;

export const EMPTY_FILTERS: FilterState = {
  therapeuticGroup: [],
  drugClass: [],
  drugFamily: [],
  formulation: [],
};

/** Facet key in the API response for each filter. */
const FACET_KEYS: Record<FilterKey, string> = {
  therapeuticGroup: 'therapeutic_group',
  drugClass: 'drug_class',
  drugFamily: 'drug_family',
  formulation: 'formulation',
};

export function FilterPanel({
  facets,
  filters,
  onChange,
}: {
  facets: Facets;
  filters: FilterState;
  onChange(next: FilterState): void;
}) {
  const { t } = useI18n();
  const panelId = useId();
  // Open by default where there is a sidebar to hold it; collapsed on a phone,
  // where the facet list would otherwise push the results off the screen.
  const [open, setOpen] = useState(
    () => typeof window !== 'undefined' && window.matchMedia('(min-width: 900px)').matches,
  );

  useEffect(() => {
    const query = window.matchMedia('(min-width: 900px)');
    const sync = (event: MediaQueryListEvent) => setOpen(event.matches);
    query.addEventListener('change', sync);
    return () => query.removeEventListener('change', sync);
  }, []);

  const labels: Record<FilterKey, string> = {
    therapeuticGroup: t.filterTherapeuticGroup,
    drugClass: t.filterDrugClass,
    drugFamily: t.filterDrugFamily,
    formulation: t.filterFormulation,
  };

  const activeCount = Object.values(filters).reduce((sum, list) => sum + list.length, 0);

  function toggle(key: FilterKey, value: string) {
    const current = filters[key];
    const next = current.includes(value)
      ? current.filter((v) => v !== value)
      : [...current, value];
    onChange({ ...filters, [key]: next });
  }

  const groups = (Object.keys(labels) as FilterKey[]).filter(
    (key) => (facets[FACET_KEYS[key]]?.length ?? 0) > 0,
  );

  return (
    <div className="filter-panel card">
      <div className="row" style={{ marginBlockEnd: 8 }}>
        <h2 style={{ margin: 0 }}>{t.filters}</h2>
        {activeCount > 0 && <span className="badge badge-accent">{activeCount}</span>}
        {/* On a phone the list of facets is long, so it collapses by default. */}
        <button
          type="button"
          className="btn btn-sm btn-secondary"
          aria-expanded={open}
          aria-controls={panelId}
          onClick={() => setOpen((v) => !v)}
        >
          {open ? t.hideFilters : t.showFilters}
        </button>
      </div>

      <div id={panelId} hidden={!open}>
        {activeCount > 0 && (
          <button
            type="button"
            className="btn btn-sm btn-secondary btn-block"
            style={{ marginBlockEnd: 12 }}
            onClick={() => onChange(EMPTY_FILTERS)}
          >
            {t.clearFilters}
          </button>
        )}

        {groups.map((key) => (
          <fieldset key={key} className="filter-group" style={{ border: 0, margin: 0, padding: 0 }}>
            <legend className="sr-only">{labels[key]}</legend>
            <h3 aria-hidden="true">{labels[key]}</h3>
            {(facets[FACET_KEYS[key]] ?? []).map((facet) => (
              <label className="checkbox-row" key={facet.value}>
                <input
                  type="checkbox"
                  checked={filters[key].includes(facet.value)}
                  onChange={() => toggle(key, facet.value)}
                />
                <span>{facet.value}</span>
                <span className="filter-count" aria-hidden="true">
                  {facet.count}
                </span>
                <span className="sr-only">
                  {labels[key]}, {facet.count}
                </span>
              </label>
            ))}
          </fieldset>
        ))}

        {groups.length === 0 && <p className="muted small">—</p>}
      </div>
    </div>
  );
}
