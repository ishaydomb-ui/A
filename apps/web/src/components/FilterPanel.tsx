import { useId, useRef } from 'react';
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

/**
 * `variant` is decided by the caller (from an actual breakpoint check, see
 * useMediaQuery), not computed in here — a wide screen gets a persistent
 * sidebar; a narrow one gets a compact "Filters" button that opens the same
 * groups in a bottom sheet. Only one variant is ever rendered, so there is
 * never a second, hidden copy of the same checkboxes sitting in the DOM.
 */
export function FilterPanel({
  facets,
  filters,
  onChange,
  variant,
}: {
  facets: Facets;
  filters: FilterState;
  onChange(next: FilterState): void;
  variant: 'sidebar' | 'trigger';
}) {
  const { t } = useI18n();
  const panelId = useId();
  const sheetRef = useRef<HTMLDialogElement>(null);

  // Switching from the sheet variant to the sidebar variant mid-session (the
  // viewport crossing the breakpoint) unmounts this component entirely —
  // React tears down the <dialog> along with it, so there is no "close the
  // sheet first" step needed here.

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

  const groupList = (
    <>
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
    </>
  );

  if (variant === 'sidebar') {
    return (
      <div className="filter-panel filter-panel-desktop card">
        <div className="row" style={{ marginBlockEnd: 8 }}>
          <h2 style={{ margin: 0 }}>{t.filters}</h2>
          {activeCount > 0 && <span className="badge badge-accent">{activeCount}</span>}
        </div>
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
        {groupList}
      </div>
    );
  }

  return (
    <>
      <button
        type="button"
        className="btn btn-secondary filters-trigger"
        aria-haspopup="dialog"
        onClick={() => sheetRef.current?.showModal()}
      >
        {t.filters}
        {activeCount > 0 && <span className="count-badge">{activeCount}</span>}
      </button>

      <dialog
        className="sheet"
        ref={sheetRef}
        aria-labelledby={panelId}
        onClick={(event) => {
          // A click that lands on the <dialog> element itself, rather than
          // anything inside it, is a click on the backdrop.
          if (event.target === sheetRef.current) sheetRef.current.close();
        }}
      >
        <div className="sheet-header">
          <h2 id={panelId}>{t.filters}</h2>
          <button
            type="button"
            className="btn btn-sm btn-secondary"
            aria-label={t.closeFilters}
            onClick={() => sheetRef.current?.close()}
          >
            <span aria-hidden="true">×</span>
          </button>
        </div>
        <div className="sheet-body">{groupList}</div>
        <div className="sheet-footer">
          {activeCount > 0 && (
            <button type="button" className="btn btn-secondary" onClick={() => onChange(EMPTY_FILTERS)}>
              {t.clearFilters}
            </button>
          )}
          <button type="button" className="btn btn-primary" onClick={() => sheetRef.current?.close()}>
            {t.close}
          </button>
        </div>
      </dialog>
    </>
  );
}
