import { useEffect, useState } from 'react';
import { LogIn, LogOut, Mail, X, Loader2 } from 'lucide-react';
import { authApi } from '../lib/api.js';

const MODE = {
  CHECKING: 'checking',
  SIGNED_OUT: 'signed-out',
  SIGNING_IN: 'signing-in',
  REGISTERING: 'registering',
  CHECK_EMAIL: 'check-email',
  SIGNED_IN: 'signed-in',
};

export default function AuthPanel() {
  const [mode, setMode] = useState(MODE.CHECKING);
  const [user, setUser] = useState(null);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [info, setInfo] = useState('');
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    authApi.me(controller.signal)
      .then((payload) => {
        if (payload.authenticated) {
          setUser(payload.user);
          setMode(MODE.SIGNED_IN);
        } else {
          setMode(MODE.SIGNED_OUT);
        }
      })
      .catch((e) => { if (e.name !== 'AbortError') setMode(MODE.SIGNED_OUT); });
    return () => controller.abort();
  }, []);

  function resetForm() {
    setEmail('');
    setPassword('');
    setError('');
    setInfo('');
  }

  function openSignIn() {
    resetForm();
    setMode(MODE.SIGNING_IN);
  }

  function openRegister() {
    resetForm();
    setMode(MODE.REGISTERING);
  }

  function closeForm() {
    resetForm();
    setMode(user ? MODE.SIGNED_IN : MODE.SIGNED_OUT);
  }

  async function handleSignIn(event) {
    event.preventDefault();
    setSubmitting(true);
    setError('');
    try {
      const loggedInUser = await authApi.login(email, password);
      setUser(loggedInUser);
      resetForm();
      setMode(MODE.SIGNED_IN);
    } catch (e) {
      setError(e.message);
    } finally {
      setSubmitting(false);
    }
  }

  async function handleRegister(event) {
    event.preventDefault();
    setSubmitting(true);
    setError('');
    try {
      await authApi.register(email, password);
      setInfo(`Check ${email} for a verification link, then sign in.`);
      setMode(MODE.CHECK_EMAIL);
    } catch (e) {
      setError(e.message);
    } finally {
      setSubmitting(false);
    }
  }

  async function handleLogout() {
    setSubmitting(true);
    try {
      await authApi.logout();
    } catch (e) {
      console.error(e);
    } finally {
      setUser(null);
      resetForm();
      setMode(MODE.SIGNED_OUT);
      setSubmitting(false);
    }
  }

  async function handleResend() {
    if (!email) return;
    setSubmitting(true);
    setError('');
    setInfo('');
    try {
      const payload = await authApi.resendVerification(email);
      setInfo(payload.message);
    } catch (e) {
      setError(e.message);
    } finally {
      setSubmitting(false);
    }
  }

  if (mode === MODE.CHECKING) {
    return <div className="auth-panel auth-panel--checking" />;
  }

  if (mode === MODE.SIGNED_IN && user) {
    return (
      <div className="auth-panel auth-panel--signed-in">
        <div className="auth-user"><Mail size={13} /><span title={user.email}>{user.email}</span></div>
        <button className="icon-button" onClick={handleLogout} disabled={submitting} aria-label="Log out"><LogOut size={15} /></button>
      </div>
    );
  }

  if (mode === MODE.SIGNED_OUT) {
    return (
      <button className="auth-trigger" onClick={openSignIn}>
        <LogIn size={14} /> Sign in
      </button>
    );
  }

  if (mode === MODE.CHECK_EMAIL) {
    return (
      <div className="auth-panel auth-panel--form">
        <div className="auth-panel-head">
          <strong>Check your email</strong>
          <button className="icon-button" onClick={closeForm} aria-label="Close"><X size={14} /></button>
        </div>
        <p className="auth-info">{info}</p>
        <button type="button" className="auth-link" onClick={openSignIn}>Back to sign in</button>
      </div>
    );
  }

  const isRegister = mode === MODE.REGISTERING;

  return (
    <div className="auth-panel auth-panel--form">
      <div className="auth-panel-head">
        <strong>{isRegister ? 'Create account' : 'Sign in'}</strong>
        <button className="icon-button" onClick={closeForm} aria-label="Close"><X size={14} /></button>
      </div>
      <form onSubmit={isRegister ? handleRegister : handleSignIn}>
        <input
          type="email"
          placeholder="Email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
          autoComplete="email"
        />
        <input
          type="password"
          placeholder="Password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
          minLength={8}
          autoComplete={isRegister ? 'new-password' : 'current-password'}
        />
        {error && <div className="auth-error">{error}</div>}
        {info && <div className="auth-info">{info}</div>}
        <button type="submit" className="auth-submit" disabled={submitting}>
          {submitting ? <Loader2 size={14} className="spin" /> : (isRegister ? 'Create account' : 'Sign in')}
        </button>
      </form>
      {!isRegister && (
        <button type="button" className="auth-link" onClick={handleResend} disabled={submitting || !email}>
          Resend verification email
        </button>
      )}
      <button type="button" className="auth-link" onClick={isRegister ? openSignIn : openRegister}>
        {isRegister ? 'Already have an account? Sign in' : "Don't have an account? Create one"}
      </button>
    </div>
  );
}
