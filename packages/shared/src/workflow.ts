import type { Capability, Role } from './roles.js';
import { roleHasCapability } from './roles.js';

/**
 * Editorial lifecycle. A record is only visible to physicians in `published`.
 * Every transition is recorded in the revision history with actor and reason.
 */
export const WORKFLOW_STATES = [
  'draft',
  'in_clinical_review',
  'changes_requested',
  'approved',
  'published',
  'archived',
] as const;
export type WorkflowState = (typeof WORKFLOW_STATES)[number];

export const WORKFLOW_STATE_LABELS: Record<WorkflowState, { en: string; he: string }> = {
  draft: { en: 'Draft', he: 'טיוטה' },
  in_clinical_review: { en: 'In clinical review', he: 'בבדיקה קלינית' },
  changes_requested: { en: 'Changes requested', he: 'נדרשים תיקונים' },
  approved: { en: 'Approved', he: 'מאושר' },
  published: { en: 'Published', he: 'פורסם' },
  archived: { en: 'Archived', he: 'בארכיון' },
};

export interface Transition {
  from: WorkflowState;
  to: WorkflowState;
  capability: Capability;
  /** A free-text reason is mandatory for this transition. */
  reasonRequired: boolean;
}

export const TRANSITIONS: readonly Transition[] = [
  { from: 'draft', to: 'in_clinical_review', capability: 'catalogue:submit_for_review', reasonRequired: false },
  { from: 'changes_requested', to: 'in_clinical_review', capability: 'catalogue:submit_for_review', reasonRequired: false },
  { from: 'in_clinical_review', to: 'approved', capability: 'catalogue:clinical_review', reasonRequired: false },
  { from: 'in_clinical_review', to: 'changes_requested', capability: 'catalogue:clinical_review', reasonRequired: true },
  { from: 'approved', to: 'published', capability: 'catalogue:publish', reasonRequired: false },
  { from: 'approved', to: 'in_clinical_review', capability: 'catalogue:clinical_review', reasonRequired: true },
  { from: 'published', to: 'draft', capability: 'catalogue:edit_draft', reasonRequired: true },
  { from: 'published', to: 'archived', capability: 'catalogue:publish', reasonRequired: true },
  { from: 'draft', to: 'archived', capability: 'catalogue:publish', reasonRequired: true },
];

export function findTransition(from: WorkflowState, to: WorkflowState): Transition | undefined {
  return TRANSITIONS.find((t) => t.from === from && t.to === to);
}

export type TransitionCheck =
  | { ok: true; transition: Transition }
  | { ok: false; error: 'invalid_transition' | 'forbidden' | 'reason_required' };

/**
 * The authoritative transition check. The API calls this before mutating any
 * record; the UI calls it to decide which buttons to render.
 */
export function checkTransition(
  role: Role,
  from: WorkflowState,
  to: WorkflowState,
  reason?: string | null,
): TransitionCheck {
  const transition = findTransition(from, to);
  if (!transition) return { ok: false, error: 'invalid_transition' };
  if (!roleHasCapability(role, transition.capability)) return { ok: false, error: 'forbidden' };
  if (transition.reasonRequired && !reason?.trim()) return { ok: false, error: 'reason_required' };
  return { ok: true, transition };
}

export function allowedTransitions(role: Role, from: WorkflowState): Transition[] {
  return TRANSITIONS.filter((t) => t.from === from && roleHasCapability(role, t.capability));
}

/** States whose content a physician may read. */
export const PUBLIC_STATES: readonly WorkflowState[] = ['published'];
