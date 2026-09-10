import { createContext, useContext } from 'react';
import type { Locale } from '@med/shared';

/**
 * Interface strings. Clinical CONTENT is never translated here — it is stored
 * per locale and shown exactly as a reviewer entered it. This file covers
 * labels, buttons and messages only.
 */
const EN = {
  appName: 'Medication Catalogue',
  skipToContent: 'Skip to main content',
  loading: 'Loading…',
  retry: 'Try again',
  close: 'Close',
  cancel: 'Cancel',
  save: 'Save',
  required: 'required',
  language: 'Language',
  switchToHebrew: 'עברית',
  switchToEnglish: 'English',

  // Navigation
  navSearch: 'Catalogue',
  navReview: 'Review',
  navImports: 'Imports',
  navUsers: 'Users',
  navAudit: 'Audit log',
  navSignOut: 'Sign out',
  navAccount: 'Account',
  mainNavigation: 'Main navigation',

  // Auth
  signIn: 'Sign in',
  signInHeading: 'Sign in to the catalogue',
  email: 'Email address',
  password: 'Password',
  currentPassword: 'Current password',
  newPassword: 'New password',
  confirmPassword: 'Confirm new password',
  forgotPassword: 'Forgotten your password?',
  forgotHeading: 'Reset your password',
  forgotHelp: 'Enter your email address and we will send you a reset link.',
  sendResetLink: 'Send reset link',
  resetHeading: 'Choose a new password',
  setPassword: 'Set password',
  acceptHeading: 'Set up your account',
  acceptHelp: 'Choose a password to finish setting up your account.',
  accessByInvitation: 'Accounts are created by an administrator. There is no public registration.',
  mfaHeading: 'Two-factor authentication',
  mfaHelp: 'Enter the 6-digit code from your authenticator app.',
  mfaCode: 'Authentication code',
  mfaVerify: 'Verify',
  mfaEnrollHeading: 'Set up two-factor authentication',
  mfaEnrollHelp:
    'Your role requires two-factor authentication. Scan this code with an authenticator app, then enter the code it shows.',
  mfaSecretManual: 'Or enter this key manually:',
  mfaOpenAuthenticator: 'Open my authenticator app',
  mfaOpenAuthenticatorHint:
    'Adds the account automatically, then come back here and enter the 6-digit code it shows.',
  mfaOtherDevice: 'Setting it up on another device instead',
  mfaCodeHint: 'Six digits from your authenticator app — not the setup key above.',
  mfaRecoveryHeading: 'Save your recovery codes',
  mfaRecoveryHelp:
    'Each code can be used once if you lose your authenticator. Store them somewhere safe — they will not be shown again.',
  mfaRecoveryAcknowledge: 'I have saved these codes',
  passwordsDoNotMatch: 'The two passwords do not match.',
  passwordMinLength: 'Use at least 12 characters.',
  signedInAs: 'Signed in as',
  changePassword: 'Change password',
  passwordChanged: 'Your password has been changed.',

  // Search
  searchLabel: 'Search medications',
  searchPlaceholder: 'Generic name, trade name, class, indication…',
  searchHint: 'Search by generic name, trade name, group, indication, mechanism or keyword.',
  searchSubmit: 'Search',
  clearSearch: 'Clear search',
  resultsCount: (n: number) => (n === 1 ? '1 medication' : `${n} medications`),
  noResults: 'No medications matched your search.',
  noResultsHint: 'Check the spelling, or try a generic name, trade name or drug class.',
  approximateMatch: 'No exact match. Showing closest results for',
  filters: 'Filters',
  showFilters: 'Show filters',
  hideFilters: 'Hide filters',
  clearFilters: 'Clear all filters',
  activeFilters: 'Active filters',
  filterTherapeuticGroup: 'Therapeutic group',
  filterDrugClass: 'Drug class',
  filterDrugFamily: 'Drug family',
  filterFormulation: 'Formulation',
  loadMore: 'Load more',
  resultsRegion: 'Search results',

  // Detail
  detailRegion: 'Medication details',
  openDetails: 'Open details for',
  backToResults: 'Back to results',
  sources: 'Sources',
  sourceLabel: 'Source',
  page: 'Page',
  jurisdiction: 'Jurisdiction',
  approvalStatus: 'Approval status',
  reviewDate: 'Review date',
  noSources: 'No source has been attached to this field yet.',
  validationStatus: 'Validation status',
  lastPublished: 'Published',
  versionLabel: 'Version',
  showingOtherLanguage: 'Shown in English',
  showingOtherLanguageHe: 'Shown in Hebrew',

  // Compare
  compare: 'Compare',
  compareAdd: 'Add to comparison',
  compareRemove: 'Remove from comparison',
  compareOpen: 'Compare selected',
  compareHeading: 'Comparison',
  compareTrayLabel: 'Selected for comparison',
  compareLimit: (n: number) => `You can compare up to ${n} medications.`,
  compareEmpty: 'Select at least two medications to compare.',
  compareClear: 'Clear comparison',

  // Disclaimer / legal
  disclaimerShort:
    'A professional reference aid. It does not replace clinical judgement or current prescribing information.',
  disclaimerHeading: 'Important',
  evaluationHeading: 'Evaluation copy —',
  unvalidatedRecord: 'Not clinically reviewed',
  unvalidatedRecordDetail:
    'This record was published for evaluation before clinical review was complete. It has not been checked against an authoritative source. Do not use it for clinical decisions.',
  outstandingChecks: 'Outstanding checks',
  sourceLookupHeading: 'Find a source',
  sourceLookupIntro:
    'Where this medication is documented in external registries. Nothing is attached until you read the document and confirm it.',
  sourceLookupNone: 'No documents found for this medication.',
  sourceLookupUnavailable: (provider: string) => `${provider} could not be checked.`,
  sourceLookupOpen: 'Read it',
  sourceLookupUse: 'Use this',
  sourceLookupAttach: 'Attach as a source',
  sourceLookupField: 'Which claim does it support?',
  sourceLookupSection: 'Section or page',
  sourceLookupSectionHint: 'Where in the document the claim appears, for example 4.2.',
  sourceLookupJurisdictionHint: 'The jurisdiction you are claiming this for — not the document\u2019s.',
  sourceLookupStatusHint:
    'Whether the use is approved in that jurisdiction. The document cannot answer this for you.',
  sourceLookupJurisdictionWarning: (jurisdiction: string) =>
    `This document governs ${jurisdiction}. It is evidence about the medicine, not about what is approved elsewhere. Confirm the status for your own jurisdiction before attaching it.`,
  sourceLookupConfirm: 'Attach source',
  sourceLookupResponsibility: 'This citation will be recorded against your account.',
  privacyPolicy: 'Privacy Policy',
  terms: 'Terms of Use',
  contact: 'Contact',
  pendingApproval: 'Awaiting approval from the system owner.',
  noPatientData: 'Do not enter patient-identifiable information anywhere in this system.',

  // Errors
  errorGeneric: 'Something went wrong. Please try again.',
  errorNotFound: 'That page could not be found.',
  errorForbidden: 'You do not have permission to view that.',
  errorOffline: 'Cannot reach the server. Check your connection.',
  sessionExpired: 'Your session has expired. Please sign in again.',
} as const;


const HE = {
  appName: 'קטלוג תרופות',
  skipToContent: 'דילוג לתוכן הראשי',
  loading: 'טוען…',
  retry: 'נסה שוב',
  close: 'סגירה',
  cancel: 'ביטול',
  save: 'שמירה',
  required: 'שדה חובה',
  language: 'שפה',
  switchToHebrew: 'עברית',
  switchToEnglish: 'English',

  navSearch: 'קטלוג',
  navReview: 'בדיקה',
  navImports: 'ייבוא',
  navUsers: 'משתמשים',
  navAudit: 'יומן ביקורת',
  navSignOut: 'התנתקות',
  navAccount: 'החשבון שלי',
  mainNavigation: 'ניווט ראשי',

  signIn: 'כניסה',
  signInHeading: 'כניסה לקטלוג',
  email: 'כתובת אימייל',
  password: 'סיסמה',
  currentPassword: 'סיסמה נוכחית',
  newPassword: 'סיסמה חדשה',
  confirmPassword: 'אימות סיסמה חדשה',
  forgotPassword: 'שכחת סיסמה?',
  forgotHeading: 'איפוס סיסמה',
  forgotHelp: 'הזן את כתובת האימייל שלך ונשלח קישור לאיפוס.',
  sendResetLink: 'שליחת קישור לאיפוס',
  resetHeading: 'בחירת סיסמה חדשה',
  setPassword: 'קביעת סיסמה',
  acceptHeading: 'הגדרת החשבון',
  acceptHelp: 'בחר סיסמה כדי להשלים את הגדרת החשבון.',
  accessByInvitation: 'החשבונות נוצרים בידי מנהל המערכת. אין הרשמה ציבורית.',
  mfaHeading: 'אימות דו-שלבי',
  mfaHelp: 'הזן את הקוד בן 6 הספרות מאפליקציית האימות.',
  mfaCode: 'קוד אימות',
  mfaVerify: 'אימות',
  mfaEnrollHeading: 'הגדרת אימות דו-שלבי',
  mfaEnrollHelp:
    'התפקיד שלך מחייב אימות דו-שלבי. סרוק את הקוד באפליקציית אימות והזן את הקוד שמוצג בה.',
  mfaSecretManual: 'לחלופין, הזן מפתח זה ידנית:',
  mfaOpenAuthenticator: 'פתיחת אפליקציית האימות',
  mfaOpenAuthenticatorHint:
    'החשבון יתווסף אוטומטית. חזור לכאן והזן את הקוד בן 6 הספרות שמוצג בה.',
  mfaOtherDevice: 'הגדרה במכשיר אחר',
  mfaCodeHint: 'שש ספרות מאפליקציית האימות — לא המפתח שמופיע למעלה.',
  mfaRecoveryHeading: 'שמור את קודי השחזור',
  mfaRecoveryHelp:
    'כל קוד ניתן לשימוש פעם אחת אם אפליקציית האימות אינה זמינה. שמור אותם במקום בטוח — הם לא יוצגו שוב.',
  mfaRecoveryAcknowledge: 'שמרתי את הקודים',
  passwordsDoNotMatch: 'הסיסמאות אינן זהות.',
  passwordMinLength: 'יש להשתמש ב-12 תווים לפחות.',
  signedInAs: 'מחובר כ-',
  changePassword: 'שינוי סיסמה',
  passwordChanged: 'הסיסמה שונתה.',

  searchLabel: 'חיפוש תרופות',
  searchPlaceholder: 'שם גנרי, שם מסחרי, קבוצה, התוויה…',
  searchHint: 'ניתן לחפש לפי שם גנרי, שם מסחרי, קבוצה, התוויה, מנגנון או מילת מפתח.',
  searchSubmit: 'חיפוש',
  clearSearch: 'ניקוי החיפוש',
  resultsCount: (n: number) => (n === 1 ? 'תרופה אחת' : `${n} תרופות`),
  noResults: 'לא נמצאו תרופות התואמות לחיפוש.',
  noResultsHint: 'בדוק את האיות, או נסה שם גנרי, שם מסחרי או מחלקת תרופות.',
  approximateMatch: 'לא נמצאה התאמה מדויקת. מוצגות התוצאות הקרובות ביותר עבור',
  filters: 'מסננים',
  showFilters: 'הצגת מסננים',
  hideFilters: 'הסתרת מסננים',
  clearFilters: 'ניקוי כל המסננים',
  activeFilters: 'מסננים פעילים',
  filterTherapeuticGroup: 'קבוצה טיפולית',
  filterDrugClass: 'מחלקת תרופות',
  filterDrugFamily: 'משפחת תרופות',
  filterFormulation: 'צורת מתן',
  loadMore: 'טעינת עוד',
  resultsRegion: 'תוצאות החיפוש',

  detailRegion: 'פרטי התרופה',
  openDetails: 'פתיחת פרטים עבור',
  backToResults: 'חזרה לתוצאות',
  sources: 'מקורות',
  sourceLabel: 'מקור',
  page: 'עמוד',
  jurisdiction: 'תחום שיפוט',
  approvalStatus: 'סטטוס אישור',
  reviewDate: 'תאריך בדיקה',
  noSources: 'טרם צורף מקור לשדה זה.',
  validationStatus: 'סטטוס אימות',
  lastPublished: 'פורסם',
  versionLabel: 'גרסה',
  showingOtherLanguage: 'מוצג באנגלית',
  showingOtherLanguageHe: 'מוצג בעברית',

  compare: 'השוואה',
  compareAdd: 'הוספה להשוואה',
  compareRemove: 'הסרה מההשוואה',
  compareOpen: 'השוואת הנבחרות',
  compareHeading: 'השוואה',
  compareTrayLabel: 'נבחרו להשוואה',
  compareLimit: (n: number) => `ניתן להשוות עד ${n} תרופות.`,
  compareEmpty: 'יש לבחור לפחות שתי תרופות להשוואה.',
  compareClear: 'ניקוי ההשוואה',

  disclaimerShort:
    'כלי עזר מקצועי. אינו מחליף שיקול דעת קליני או מידע מרשם עדכני.',
  disclaimerHeading: 'חשוב',
  evaluationHeading: 'גרסת הערכה —',
  unvalidatedRecord: 'לא עבר בדיקה קלינית',
  unvalidatedRecordDetail:
    'רשומה זו פורסמה לצורכי הערכה לפני השלמת הבדיקה הקלינית. היא לא הושוותה למקור מוסמך. אין להסתמך עליה לקבלת החלטות קליניות.',
  outstandingChecks: 'בדיקות שטרם הושלמו',
  sourceLookupHeading: 'איתור מקור',
  sourceLookupIntro:
    'היכן התרופה מתועדת במאגרים חיצוניים. שום דבר לא מצורף עד שתקרא את המסמך ותאשר.',
  sourceLookupNone: 'לא נמצאו מסמכים עבור תרופה זו.',
  sourceLookupUnavailable: (provider: string) => `לא ניתן היה לבדוק את ${provider}.`,
  sourceLookupOpen: 'קריאה',
  sourceLookupUse: 'בחירה',
  sourceLookupAttach: 'צירוף כמקור',
  sourceLookupField: 'לאיזו טענה המקור מתייחס?',
  sourceLookupSection: 'סעיף או עמוד',
  sourceLookupSectionHint: 'היכן במסמך מופיעה הטענה, למשל 4.2.',
  sourceLookupJurisdictionHint: 'תחום השיפוט שעבורו אתה טוען זאת — לא זה של המסמך.',
  sourceLookupStatusHint:
    'האם השימוש מאושר באותו תחום שיפוט. המסמך אינו יכול לענות על כך עבורך.',
  sourceLookupJurisdictionWarning: (jurisdiction: string) =>
    `מסמך זה חל על ${jurisdiction}. הוא ראיה על התרופה, לא על מה שמאושר במקום אחר. ודא את הסטטוס בתחום השיפוט שלך לפני הצירוף.`,
  sourceLookupConfirm: 'צירוף המקור',
  sourceLookupResponsibility: 'הציטוט יירשם על שמך.',
  privacyPolicy: 'מדיניות פרטיות',
  terms: 'תנאי שימוש',
  contact: 'יצירת קשר',
  pendingApproval: 'ממתין לאישור בעל המערכת.',
  noPatientData: 'אין להזין פרטים מזהים של מטופלים בשום מקום במערכת.',

  errorGeneric: 'אירעה שגיאה. נסה שוב.',
  errorNotFound: 'הדף לא נמצא.',
  errorForbidden: 'אין לך הרשאה לצפות בכך.',
  errorOffline: 'לא ניתן להגיע לשרת. בדוק את החיבור.',
  sessionExpired: 'תוקף החיבור פג. יש להתחבר מחדש.',
};

/**
 * Widens the English literal types into plain strings, so the Hebrew table is
 * checked for having exactly the same keys and shapes without every value
 * having to be the identical literal.
 */
type Widen<T> = {
  [K in keyof T]: T[K] extends (...args: infer A) => infer R ? (...args: A) => R : string;
};

export type Strings = Widen<typeof EN>;

export const STRINGS: Record<Locale, Strings> = { en: EN, he: HE };



export interface I18nValue {
  locale: Locale;
  dir: 'ltr' | 'rtl';
  t: Strings;
  setLocale(locale: Locale): void;
}

export const I18nContext = createContext<I18nValue | null>(null);

export function useI18n(): I18nValue {
  const value = useContext(I18nContext);
  if (!value) throw new Error('useI18n must be used inside the I18n provider');
  return value;
}

const STORAGE_KEY = 'medcat.locale';

export function readStoredLocale(): Locale {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === 'he' || stored === 'en') return stored;
  } catch {
    // Storage can be unavailable (private mode); fall through to the default.
  }
  return navigator.language?.startsWith('he') ? 'he' : 'en';
}

export function storeLocale(locale: Locale): void {
  try {
    localStorage.setItem(STORAGE_KEY, locale);
  } catch {
    // A stored preference is a convenience, not a requirement.
  }
}
