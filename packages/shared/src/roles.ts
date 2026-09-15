/**
 * Role model. Ordered from least to most privileged for convenience only;
 * every check is an explicit capability lookup, never a numeric comparison.
 */
export const ROLES = ['physician', 'clinical_reviewer', 'editor', 'admin'] as const;
export type Role = (typeof ROLES)[number];

export function isRole(value: unknown): value is Role {
  return typeof value === 'string' && (ROLES as readonly string[]).includes(value);
}

/**
 * Capabilities are the single source of truth for authorisation. The API
 * enforces them on every route; the UI only uses them to hide controls.
 */
export const CAPABILITIES = [
  'catalogue:read_published',
  'catalogue:read_unpublished',
  'catalogue:edit_draft',
  'catalogue:submit_for_review',
  'catalogue:clinical_review',
  'catalogue:publish',
  'import:create',
  'import:commit',
  'review:read',
  'review:resolve',
  'users:manage',
  'audit:read',
] as const;
export type Capability = (typeof CAPABILITIES)[number];

const PHYSICIAN: Capability[] = ['catalogue:read_published'];

const CLINICAL_REVIEWER: Capability[] = [
  ...PHYSICIAN,
  'catalogue:read_unpublished',
  'catalogue:clinical_review',
  'review:read',
  'review:resolve',
];

const EDITOR: Capability[] = [
  ...PHYSICIAN,
  'catalogue:read_unpublished',
  'catalogue:edit_draft',
  'catalogue:submit_for_review',
  'import:create',
  'review:read',
];

const ADMIN: Capability[] = [
  ...new Set<Capability>([
    ...EDITOR,
    ...CLINICAL_REVIEWER,
    'catalogue:publish',
    'import:commit',
    'users:manage',
    'audit:read',
  ]),
];

export const ROLE_CAPABILITIES: Record<Role, readonly Capability[]> = {
  physician: PHYSICIAN,
  clinical_reviewer: CLINICAL_REVIEWER,
  editor: EDITOR,
  admin: ADMIN,
};

export function roleHasCapability(role: Role, capability: Capability): boolean {
  return ROLE_CAPABILITIES[role]?.includes(capability) ?? false;
}

/** Roles for which multi-factor authentication is mandatory, not optional. */
export const MFA_REQUIRED_ROLES: readonly Role[] = ['admin'];

export function mfaRequiredForRole(role: Role): boolean {
  return MFA_REQUIRED_ROLES.includes(role);
}
