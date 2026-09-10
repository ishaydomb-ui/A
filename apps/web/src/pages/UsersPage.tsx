import { useCallback, useEffect, useRef, useState } from 'react';
import { ROLES } from '@med/shared';
import { useI18n } from '../i18n.ts';
import { ApiError, api } from '../lib/api.ts';
import type { PublicUser } from '../lib/types.ts';
import { Notice } from '../components/Notice.tsx';
import { Spinner } from '../components/Spinner.tsx';

export function UsersPage() {
  const { t } = useI18n();
  const [users, setUsers] = useState<PublicUser[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [invitationLink, setInvitationLink] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [email, setEmail] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [role, setRole] = useState<string>('physician');

  // The row currently being renamed, and the name typed into it. Only one row
  // is editable at a time, so a half-finished edit cannot be left behind on a
  // row that has scrolled out of sight.
  const [renaming, setRenaming] = useState<{ id: string; value: string } | null>(null);

  // Re-inviting is done from a row that can be well below the fold, while the
  // link it produces is shown with the invitation form at the top. Screen
  // readers hear it announced; bring it into view for everyone else.
  const invitationRef = useRef<HTMLDivElement>(null);

  const load = useCallback(() => {
    api
      .get<{ users: PublicUser[] }>('/api/users')
      .then((res) => setUsers(res.users))
      .catch((err: unknown) => setError(err instanceof ApiError ? err.message : t.errorGeneric));
  }, [t]);

  useEffect(load, [load]);

  useEffect(() => {
    if (invitationLink) invitationRef.current?.scrollIntoView({ block: 'center' });
  }, [invitationLink]);

  async function invite(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setInvitationLink(null);
    try {
      const result = await api.post<{ invitationLink: string }>('/api/users/invitations', {
        email: email.trim(),
        displayName: displayName.trim(),
        role,
      });
      setInvitationLink(result.invitationLink);
      setEmail('');
      setDisplayName('');
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t.errorGeneric);
    } finally {
      setBusy(false);
    }
  }

  /**
   * Issues a fresh invitation. An invitation link carries the address the API
   * is configured with, so one created before the site had a reachable address
   * points somewhere the recipient cannot open; re-inviting revokes it and
   * builds a new one.
   */
  async function resendInvitation(id: string) {
    setError(null);
    setInvitationLink(null);
    try {
      const result = await api.post<{ invitationLink: string }>(
        `/api/users/${id}/invitations/resend`,
        {},
      );
      setInvitationLink(result.invitationLink);
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t.errorGeneric);
    }
  }

  async function update(id: string, patch: Record<string, string>) {
    setError(null);
    try {
      await api.patch(`/api/users/${id}`, patch);
      setRenaming(null);
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t.errorGeneric);
    }
  }

  return (
    <>
      <h1>{t.navUsers}</h1>

      <Notice tone="info">
        There is no public registration. An account exists only because it was created here, and it
        becomes usable only when the person accepts their invitation.
      </Notice>

      {error && <Notice tone="error">{error}</Notice>}

      <section className="card" aria-labelledby="invite-heading" style={{ marginBlockEnd: 24 }}>
        <h2 id="invite-heading">Invite a clinician</h2>
        <form onSubmit={invite}>
          <div className="field">
            <label htmlFor="invite-email">{t.email}</label>
            <input
              id="invite-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
          </div>
          <div className="field">
            <label htmlFor="invite-name">Full name</label>
            <input
              id="invite-name"
              type="text"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              required
            />
          </div>
          <div className="field">
            <label htmlFor="invite-role">Role</label>
            <select id="invite-role" value={role} onChange={(e) => setRole(e.target.value)}>
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {r.replace(/_/g, ' ')}
                </option>
              ))}
            </select>
            <p className="hint">Administrators must set up two-factor authentication before they can sign in.</p>
          </div>
          <button type="submit" className="btn btn-primary" disabled={busy}>
            {busy ? t.loading : 'Send invitation'}
          </button>
        </form>

        <div ref={invitationRef}>
          {invitationLink && (
            <Notice tone="success" title="Invitation created">
              <p>
                If no mail server is configured the message is written to the server's outbox
                instead of being sent. Deliver this single-use link to the person yourself:
              </p>
              <p className="mono" style={{ wordBreak: 'break-all' }}>
                {invitationLink}
              </p>
            </Notice>
          )}
        </div>
      </section>

      <section aria-labelledby="users-heading">
        <h2 id="users-heading">Accounts</h2>
        {!users ? (
          <Spinner />
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th scope="col">Name</th>
                  <th scope="col">{t.email}</th>
                  <th scope="col">Role</th>
                  <th scope="col">Status</th>
                  <th scope="col">MFA</th>
                  <th scope="col">Actions</th>
                </tr>
              </thead>
              <tbody>
                {users.map((user) => (
                  <tr key={user.id}>
                    <th scope="row">
                      {renaming?.id === user.id ? (
                        <form
                          className="row name-edit"
                          onSubmit={(e) => {
                            e.preventDefault();
                            const value = renaming.value.trim();
                            if (value) void update(user.id, { displayName: value });
                          }}
                        >
                          <label htmlFor={`name-${user.id}`} className="sr-only">
                            Full name for {user.email}
                          </label>
                          <input
                            id={`name-${user.id}`}
                            type="text"
                            value={renaming.value}
                            autoFocus
                            required
                            maxLength={200}
                            onChange={(e) => setRenaming({ id: user.id, value: e.target.value })}
                            onKeyDown={(e) => {
                              if (e.key === 'Escape') setRenaming(null);
                            }}
                          />
                          <button type="submit" className="btn btn-sm btn-primary">
                            Save
                          </button>
                          <button
                            type="button"
                            className="btn btn-sm btn-secondary"
                            onClick={() => setRenaming(null)}
                          >
                            {t.cancel}
                          </button>
                        </form>
                      ) : (
                        <span className="row">
                          {user.displayName}
                          <button
                            type="button"
                            className="btn btn-sm btn-secondary"
                            aria-label={`Rename ${user.displayName}`}
                            onClick={() => setRenaming({ id: user.id, value: user.displayName })}
                          >
                            Rename
                          </button>
                        </span>
                      )}
                    </th>
                    <td className="small">{user.email}</td>
                    <td>
                      <label htmlFor={`role-${user.id}`} className="sr-only">
                        Role for {user.displayName}
                      </label>
                      <select
                        id={`role-${user.id}`}
                        value={user.role}
                        onChange={(e) => void update(user.id, { role: e.target.value })}
                      >
                        {ROLES.map((r) => (
                          <option key={r} value={r}>
                            {r.replace(/_/g, ' ')}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <span
                        className={`badge ${user.status === 'active' ? 'badge-success' : user.status === 'invited' ? 'badge-medium' : 'badge-high'}`}
                      >
                        {user.status}
                      </span>
                    </td>
                    <td>
                      {user.mfaEnabled ? (
                        <span className="badge badge-success">on</span>
                      ) : (
                        <span className="badge">off</span>
                      )}
                    </td>
                    <td>
                      <div className="row">
                        {user.status === 'active' ? (
                          <button
                            type="button"
                            className="btn btn-sm btn-danger"
                            onClick={() => void update(user.id, { status: 'suspended' })}
                          >
                            Suspend
                          </button>
                        ) : user.status === 'suspended' ? (
                          <button
                            type="button"
                            className="btn btn-sm btn-secondary"
                            onClick={() => void update(user.id, { status: 'active' })}
                          >
                            Reinstate
                          </button>
                        ) : user.status === 'invited' ? (
                          <button
                            type="button"
                            className="btn btn-sm btn-secondary"
                            onClick={() => void resendInvitation(user.id)}
                          >
                            Re-invite
                          </button>
                        ) : null}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}
