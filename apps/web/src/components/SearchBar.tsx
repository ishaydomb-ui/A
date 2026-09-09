import { useEffect, useId, useRef, useState } from 'react';
import { useI18n } from '../i18n.ts';
import { api, qs } from '../lib/api.ts';

interface Suggestion {
  slug: string;
  label: string;
  kind: string;
}

/**
 * Search input with type-ahead, implemented as an ARIA combobox so the
 * suggestion list is announced and fully keyboard operable.
 */
export function SearchBar({
  value,
  onChange,
  onSubmit,
  includeUnpublished,
}: {
  value: string;
  onChange(next: string): void;
  onSubmit(query: string): void;
  includeUnpublished: boolean;
}) {
  const { t, locale } = useI18n();
  const listId = useId();
  const inputId = useId();
  const hintId = useId();

  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  // Suppresses the fetch that a programmatic value change would otherwise
  // trigger right after picking a suggestion.
  const skipNextFetch = useRef(false);

  useEffect(() => {
    if (skipNextFetch.current) {
      skipNextFetch.current = false;
      return;
    }
    if (value.trim().length < 2) {
      setSuggestions([]);
      setOpen(false);
      return;
    }
    const controller = new AbortController();
    const timer = setTimeout(() => {
      api
        .get<{ suggestions: Suggestion[] }>(
          `/api/search/suggest${qs({ q: value, locale, includeUnpublished })}`,
          controller.signal,
        )
        .then((res) => {
          setSuggestions(res.suggestions);
          setOpen(res.suggestions.length > 0);
          setActive(-1);
        })
        .catch(() => undefined);
    }, 180);

    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [value, locale, includeUnpublished]);

  useEffect(() => {
    function onDocumentClick(event: MouseEvent) {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener('mousedown', onDocumentClick);
    return () => document.removeEventListener('mousedown', onDocumentClick);
  }, []);

  function choose(suggestion: Suggestion) {
    skipNextFetch.current = true;
    onChange(suggestion.label);
    setOpen(false);
    setActive(-1);
    onSubmit(suggestion.label);
    inputRef.current?.focus();
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'Escape') {
      setOpen(false);
      setActive(-1);
      return;
    }
    if (!open || suggestions.length === 0) return;

    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setActive((i) => (i + 1) % suggestions.length);
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setActive((i) => (i <= 0 ? suggestions.length - 1 : i - 1));
    } else if (event.key === 'Enter' && active >= 0) {
      event.preventDefault();
      choose(suggestions[active]);
    }
  }

  return (
    <div ref={containerRef}>
      <label htmlFor={inputId} className="sr-only">
        {t.searchLabel}
      </label>
      <form
        className="search-bar"
        role="search"
        onSubmit={(event) => {
          event.preventDefault();
          setOpen(false);
          onSubmit(value);
        }}
      >
        <input
          id={inputId}
          ref={inputRef}
          type="search"
          value={value}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={onKeyDown}
          placeholder={t.searchPlaceholder}
          aria-describedby={hintId}
          role="combobox"
          aria-expanded={open}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={active >= 0 ? `${listId}-${active}` : undefined}
          autoComplete="off"
          spellCheck={false}
          enterKeyHint="search"
        />
        {value && (
          <button
            type="button"
            className="search-clear"
            onClick={() => {
              onChange('');
              onSubmit('');
              inputRef.current?.focus();
            }}
            aria-label={t.clearSearch}
          >
            ×
          </button>
        )}
        <button type="submit" className="btn btn-primary">
          {t.searchSubmit}
        </button>

        {open && (
          <ul className="suggestions" id={listId} role="listbox" aria-label={t.searchLabel}>
            {suggestions.map((suggestion, i) => (
              <li key={`${suggestion.slug}-${suggestion.label}`} role="presentation">
                <button
                  type="button"
                  id={`${listId}-${i}`}
                  role="option"
                  aria-selected={i === active}
                  className="suggestion"
                  // mousedown fires before the input's blur, so the click is
                  // not lost to the list closing first.
                  onMouseDown={(event) => {
                    event.preventDefault();
                    choose(suggestion);
                  }}
                  onMouseEnter={() => setActive(i)}
                >
                  <span>{suggestion.label}</span>
                  <span className="kind">{suggestion.kind.replace(/_/g, ' ')}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </form>
      <p id={hintId} className="hint small muted" style={{ marginBlock: 4 }}>
        {t.searchHint}
      </p>
    </div>
  );
}
