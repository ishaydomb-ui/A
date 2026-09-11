import { useCallback, useEffect, useMemo, useState } from 'react';
import { Route, Routes } from 'react-router-dom';
import { DIRECTION, type Locale } from '@med/shared';
import { I18nContext, STRINGS, readStoredLocale, storeLocale } from './i18n.ts';
import { AuthProvider, useAuth } from './lib/auth.tsx';
import { ThemeContext, readStoredTheme, storeTheme, type Theme } from './lib/theme.ts';
import { Layout } from './components/Layout.tsx';
import { CompareSelectionProvider } from './components/CompareSelectionProvider.tsx';
import { SavedProvider } from './components/SavedProvider.tsx';
import { RequireAuth } from './components/RequireAuth.tsx';
import { RequireCapability } from './components/RequireCapability.tsx';
import { Spinner } from './components/Spinner.tsx';
import { SignInPage } from './pages/SignInPage.tsx';
import { AcceptInvitationPage } from './pages/AcceptInvitationPage.tsx';
import { ForgotPasswordPage } from './pages/ForgotPasswordPage.tsx';
import { ResetPasswordPage } from './pages/ResetPasswordPage.tsx';
import { ExplorePage } from './pages/ExplorePage.tsx';
import { SearchPage } from './pages/SearchPage.tsx';
import { SavedPage } from './pages/SavedPage.tsx';
import { MedicationPage } from './pages/MedicationPage.tsx';
import { ComparePage } from './pages/ComparePage.tsx';
import { ReviewPage } from './pages/ReviewPage.tsx';
import { ImportsPage } from './pages/ImportsPage.tsx';
import { UsersPage } from './pages/UsersPage.tsx';
import { AccountPage } from './pages/AccountPage.tsx';
import { LegalPage } from './pages/LegalPage.tsx';
import { NotFoundPage } from './pages/NotFoundPage.tsx';

export function App() {
  const [locale, setLocaleState] = useState<Locale>(() => readStoredLocale());
  const [theme, setThemeState] = useState<Theme>(() => readStoredTheme());

  const setLocale = useCallback((next: Locale) => {
    setLocaleState(next);
    storeLocale(next);
  }, []);

  const setTheme = useCallback((next: Theme) => {
    setThemeState(next);
    storeTheme(next);
  }, []);

  // The document's language and direction follow the chosen locale, so screen
  // readers announce the right language and the whole layout mirrors for
  // Hebrew without a separate stylesheet.
  useEffect(() => {
    document.documentElement.lang = locale;
    document.documentElement.dir = DIRECTION[locale];
    document.title = STRINGS[locale].appName;
  }, [locale]);

  // The theme is stamped explicitly rather than left to the device's own
  // colour-scheme setting — see readStoredTheme for why — so light always
  // applies until someone opts into dark here.
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  const i18n = useMemo(
    () => ({ locale, dir: DIRECTION[locale], t: STRINGS[locale], setLocale }),
    [locale, setLocale],
  );
  const themeValue = useMemo(() => ({ theme, setTheme }), [theme, setTheme]);

  return (
    <I18nContext.Provider value={i18n}>
      <ThemeContext.Provider value={themeValue}>
        <AuthProvider>
          <CompareSelectionProvider>
            <SavedProvider>
              <AppRoutes />
            </SavedProvider>
          </CompareSelectionProvider>
        </AuthProvider>
      </ThemeContext.Provider>
    </I18nContext.Provider>
  );
}

function AppRoutes() {
  const { loading } = useAuth();

  // Nothing is rendered until we know whether there is a session, so a
  // signed-in user never sees a flash of the sign-in screen.
  if (loading) {
    return (
      <div className="auth-shell">
        <Spinner />
      </div>
    );
  }

  return (
    <Routes>
      <Route path="/sign-in" element={<SignInPage />} />
      <Route path="/accept-invitation" element={<AcceptInvitationPage />} />
      <Route path="/forgot-password" element={<ForgotPasswordPage />} />
      <Route path="/reset-password" element={<ResetPasswordPage />} />
      <Route path="/privacy" element={<LegalPage document="privacy" />} />
      <Route path="/terms" element={<LegalPage document="terms" />} />

      <Route
        element={
          <RequireAuth>
            <Layout />
          </RequireAuth>
        }
      >
        <Route path="/" element={<ExplorePage />} />
        <Route path="/catalogue" element={<SearchPage />} />
        <Route path="/saved" element={<SavedPage />} />
        <Route path="/medications/:slug" element={<MedicationPage />} />
        <Route path="/compare" element={<ComparePage />} />
        <Route
          path="/review"
          element={
            <RequireCapability capability="review:read">
              <ReviewPage />
            </RequireCapability>
          }
        />
        <Route
          path="/imports"
          element={
            <RequireCapability capability="import:create">
              <ImportsPage />
            </RequireCapability>
          }
        />
        <Route
          path="/users"
          element={
            <RequireCapability capability="users:manage">
              <UsersPage />
            </RequireCapability>
          }
        />
        <Route path="/account" element={<AccountPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
