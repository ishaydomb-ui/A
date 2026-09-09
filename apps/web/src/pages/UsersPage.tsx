import { useCallback, useEffect, useState } from 'react';
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

  const load = useCallback(() => {
    api
      .get<{ users: PublicUser[] }>('/api/users')
      .then((res) => setUsers(res.users))
      .catch((err: unknown) => setError(err instanceof ApiError ? err.message : t.errorGeneric));
  }, [t]);

  useEffect(load, [load]);

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

  async function update(id: string, patch: Record<string, string>) {
    setError(null);
    try {
      await api.patch(`/api/users/${id}`, patch);
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

        {invitationLink && (
          <Notice tone="success" title="Invitation created">
            <p>
              If no mail server is configured the message is written to the server's outbox instead
              of being sent. Deliver this single-use link to the person yourself:
            </p>
            <p className="mono" style={{ wordBreak: 'break-all' }}>
              {invitationLink}
            </p>
          </Notice>
        )}
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
                    <th scope="row">{user.displayName}</th>
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
