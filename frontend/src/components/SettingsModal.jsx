import { useEffect, useMemo, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { X, User, Globe, Mail, Calendar, HardDrive, LogOut, Trash2, Link2, Unlink, Bell, BellRing, History, Download, ShieldCheck } from 'lucide-react';

import { useAuth } from '../hooks/useAuth';
import {
  deleteMyAccount, disconnectGoogle, exportMyData, fetchActionLog, resendVerification, getGoogleAuthUrl, getNotificationPreferences, sendTestNotification,
  setNotificationPreferences, updateTimezone,
} from '../services/api';
import { actionKind, actionSummary, statusLabel } from '../services/actionLog';
import { browserIsSubscribed, disablePush, enablePush, pushSupported } from '../services/push';

const FALLBACK_ZONES = [
  'Africa/Cairo', 'Africa/Johannesburg', 'Africa/Lagos', 'America/Chicago', 'America/Los_Angeles', 'America/New_York',
  'America/Sao_Paulo', 'Asia/Dubai', 'Asia/Kolkata', 'Asia/Riyadh', 'Asia/Shanghai', 'Asia/Tokyo',
  'Australia/Sydney', 'Europe/Berlin', 'Europe/Istanbul', 'Europe/London', 'Europe/Paris', 'UTC',
];

function browserZone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  } catch {
    return 'UTC';
  }
}

function timeZones(current) {
  let zones;
  try {
    zones = Intl.supportedValuesOf('timeZone');
  } catch {
    zones = FALLBACK_ZONES;
  }
  return zones.includes(current) ? zones : [current, ...zones];
}

const SERVICES = [
  { key: 'gmail', label: 'Gmail', icon: Mail },
  { key: 'calendar', label: 'Calendar', icon: Calendar },
  { key: 'drive', label: 'Drive', icon: HardDrive },
];

/** Account, time zone, Google connection and local data, in one dialog opened from the workspace header. */
export default function SettingsModal({ open, onClose, googleConnected, googleServices, onGoogleChanged, showSuccess, showError }) {
  const { user, signOut } = useAuth();
  const [zone, setZone] = useState(user?.timezone || browserZone());
  const [savingZone, setSavingZone] = useState(false);
  const [busy, setBusy] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);
  const zones = useMemo(() => timeZones(zone), [zone]);
  const [channels, setChannels] = useState(null);       // what the server says: { push, email, push_available, email_available }
  const [browserPush, setBrowserPush] = useState(false); // is *this* browser subscribed
  const [savingChannel, setSavingChannel] = useState(false);
  const [accountBusy, setAccountBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleteProof, setDeleteProof] = useState('');     // the password, or the email address for an account without a password
  const [activity, setActivity] = useState(null);       // the user's action log: null while loading, [] when empty, 'error'

  useEffect(() => {
    if (!open) return undefined;
    let cancelled = false;
    setActivity(null);
    fetchActionLog(15).then(
      (rows) => { if (!cancelled) setActivity(rows); },
      () => { if (!cancelled) setActivity('error'); },
    );
    return () => { cancelled = true; };
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    let cancelled = false;
    getNotificationPreferences().then(
      (prefs) => { if (!cancelled) setChannels(prefs); },
      () => { if (!cancelled) setChannels(null); },
    );
    browserIsSubscribed().then((on) => { if (!cancelled) setBrowserPush(on); }, () => {});
    return () => { cancelled = true; };
  }, [open]);

  const togglePush = async () => {
    setSavingChannel(true);
    try {
      if (channels.push && browserPush) {
        await disablePush();
        setBrowserPush(false);
        // The channel stays on for the user's other browsers; switch it off only when none is left.
        if (channels.push_browsers <= 1) setChannels(await setNotificationPreferences({ push: false }));
        else setChannels(await getNotificationPreferences());
        showSuccess('Push notifications are off for this browser.');
      } else {
        await enablePush();
        setBrowserPush(true);
        setChannels(await setNotificationPreferences({ push: true }));
        showSuccess('Push notifications are on for this browser.');
      }
    } catch (err) {
      showError(err?.message || 'Could not change push notifications.');
    } finally {
      setSavingChannel(false);
    }
  };

  const toggleEmail = async () => {
    setSavingChannel(true);
    try {
      setChannels(await setNotificationPreferences({ email: !channels.email }));
    } catch (err) {
      showError(err?.message || 'Could not change email notifications.');
    } finally {
      setSavingChannel(false);
    }
  };

  const sendTest = async () => {
    try {
      await sendTestNotification();
      showSuccess('Test notification sent.');
    } catch (err) {
      showError(err?.message || 'Could not send the test notification.');
    }
  };

  useEffect(() => {
    if (!open) return undefined;
    setZone(user?.timezone || browserZone());
    setConfirmClear(false);
    setConfirmDelete(false);
    setDeleteProof('');
    const onKey = (event) => { if (event.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, user, onClose]);

  const saveZone = async (next) => {
    setZone(next);
    setSavingZone(true);
    try {
      await updateTimezone(next);
      showSuccess(`Time zone set to ${next}.`);
    } catch (err) {
      showError(err?.message || 'Could not save the time zone.');
    } finally {
      setSavingZone(false);
    }
  };

  const connect = async () => {
    setBusy(true);
    try {
      const data = await getGoogleAuthUrl();
      if (data?.auth_url) window.location.href = data.auth_url;
    } catch {
      showError('Failed to start the Google connection.');
      setBusy(false);
    }
  };

  const disconnect = async () => {
    setBusy(true);
    try {
      await disconnectGoogle();
      showSuccess('Google disconnected.');
      onGoogleChanged();
    } catch (err) {
      showError(err?.message || 'Could not disconnect Google.');
    } finally {
      setBusy(false);
    }
  };

  const sendVerification = async () => {
    setAccountBusy(true);
    try {
      const result = await resendVerification();
      if (result?.already_verified) showSuccess('Your email is already confirmed.');
      else showSuccess('A new confirmation link was sent.');
    } catch (err) {
      showError(err?.message || 'Could not send the link.');
    } finally {
      setAccountBusy(false);
    }
  };

  const downloadData = async () => {
    setAccountBusy(true);
    try {
      const blob = await exportMyData();
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `nexusai-export-${new Date().toISOString().slice(0, 10)}.zip`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      showSuccess('Your data was downloaded.');
    } catch (err) {
      showError(err?.message || 'Could not export your data.');
    } finally {
      setAccountBusy(false);
    }
  };

  const deleteAccount = async () => {
    setAccountBusy(true);
    try {
      const result = await deleteMyAccount(user?.has_password === false ? { confirmEmail: deleteProof } : { password: deleteProof });
      if (result?.google_access === 'failed') {
        window.alert('Your account was deleted, but Google could not be reached to cancel access. You can remove NexusAI at myaccount.google.com/permissions.');
      }
      try { await signOut(); } catch { /* the account is gone: this browser forgets the session anyway */ }
      window.location.assign('/');
    } catch (err) {
      showError(err?.message || 'Could not delete the account.');
      setAccountBusy(false);
    }
  };

  const clearHistory = () => {
    try {
      localStorage.removeItem('nexusai_sessions');
      localStorage.removeItem('nexusai_active_session');
    } catch { /* storage unavailable: nothing to clear */ }
    window.location.reload();
  };

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 backdrop-blur-sm px-4"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}
        >
          <motion.div
            role="dialog"
            aria-modal="true"
            aria-label="Settings"
            className="w-full max-w-md rounded-2xl border border-white/10 bg-[#111] p-6 shadow-2xl max-h-[90vh] overflow-y-auto"
            initial={{ opacity: 0, y: 20, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 10, scale: 0.98 }}
            transition={{ duration: 0.2 }}
          >
            <div className="flex items-center justify-between mb-5">
              <h2 className="text-lg font-semibold text-white m-0">Settings</h2>
              <button onClick={onClose} aria-label="Close settings"
                className="p-1.5 rounded-lg text-white/40 hover:text-white hover:bg-white/5 bg-transparent border-none cursor-pointer">
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* Account */}
            <section className="mb-5">
              <p className="text-[11px] font-semibold uppercase tracking-widest text-white/30 mb-2">Account</p>
              <div className="flex items-center gap-3 rounded-xl bg-white/[0.03] border border-white/[0.06] p-3">
                <div className="flex h-10 w-10 items-center justify-center rounded-full bg-gradient-to-br from-[#00f0ff]/30 to-[#a855f7]/30">
                  <User className="w-5 h-5 text-white/80" />
                </div>
                <div className="min-w-0">
                  <p className="m-0 text-sm font-medium text-white truncate">{user?.name || 'Your account'}</p>
                  <p className="m-0 text-xs text-white/40 truncate">{user?.email}</p>
                </div>
              </div>
            </section>

            {/* Time zone */}
            <section className="mb-5">
              <p className="text-[11px] font-semibold uppercase tracking-widest text-white/30 mb-2">Time zone</p>
              <div className="flex items-center gap-2 rounded-xl bg-white/[0.03] border border-white/[0.06] p-3">
                <Globe className="w-4 h-4 text-white/40 shrink-0" />
                <select
                  value={zone}
                  disabled={savingZone}
                  onChange={(event) => saveZone(event.target.value)}
                  aria-label="Time zone"
                  className="flex-1 min-w-0 bg-transparent text-sm text-white/80 outline-none border-none cursor-pointer"
                >
                  {zones.map((name) => (
                    <option key={name} value={name} className="bg-[#111]">{name}</option>
                  ))}
                </select>
              </div>
              <p className="mt-1.5 mb-0 text-xs text-white/30">Used for "tomorrow", reminders and the morning briefing.</p>
            </section>

            {/* Notifications */}
            <section className="mb-5">
              <p className="text-[11px] font-semibold uppercase tracking-widest text-white/30 mb-2">Notifications</p>
              <div className="rounded-xl bg-white/[0.03] border border-white/[0.06] p-3 space-y-3">
                <p className="m-0 flex items-center gap-2.5 text-sm text-white/60">
                  <Bell className="w-4 h-4" /> In the app
                  <span className="ml-auto text-xs text-emerald-400/80">Always on</span>
                </p>
                <label className="flex items-center gap-2.5 text-sm text-white/60">
                  <BellRing className="w-4 h-4" /> Push, on this device
                  <input type="checkbox" aria-label="Push notifications on this device" className="ml-auto cursor-pointer"
                    checked={Boolean(channels?.push && browserPush)}
                    disabled={savingChannel || !channels || !pushSupported() || (!channels.push_available && !browserPush)}
                    onChange={togglePush} />
                </label>
                {channels && !channels.push_available && (
                  <p className="m-0 text-xs text-white/30">Push is not set up on this server yet.</p>
                )}
                {!pushSupported() && <p className="m-0 text-xs text-white/30">This browser does not support push.</p>}
                <label className="flex items-center gap-2.5 text-sm text-white/60">
                  <Mail className="w-4 h-4" /> Email to myself
                  <input type="checkbox" aria-label="Email notifications to myself" className="ml-auto cursor-pointer"
                    checked={Boolean(channels?.email)}
                    disabled={savingChannel || !channels || !channels.email_available}
                    onChange={toggleEmail} />
                </label>
                {channels && !channels.email_available && (
                  <p className="m-0 text-xs text-white/30">Connect Google to get emails. They are sent from your own Gmail to you.</p>
                )}
                <button onClick={sendTest} disabled={!channels}
                  className="w-full rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm text-white/80 hover:bg-white/10 cursor-pointer disabled:opacity-50">
                  Send a test notification
                </button>
              </div>
            </section>

            {/* Activity: what the assistant did on the user's behalf */}
            <section className="mb-5">
              <p className="text-[11px] font-semibold uppercase tracking-widest text-white/30 mb-2">Activity</p>
              <div className="rounded-xl bg-white/[0.03] border border-white/[0.06] p-3">
                {activity === null && <p className="m-0 text-xs text-white/30">Loading…</p>}
                {activity === 'error' && <p className="m-0 text-xs text-red-300/70">Could not load your activity.</p>}
                {Array.isArray(activity) && activity.length === 0 && (
                  <p className="m-0 text-xs text-white/30">Nothing yet. Emails, events, reminders and files the assistant handles for you are listed here, with when you approved them.</p>
                )}
                {Array.isArray(activity) && activity.length > 0 && (
                  <ul className="list-none m-0 p-0 space-y-2.5 max-h-56 overflow-y-auto" aria-label="Activity log">
                    {activity.map((row) => {
                      const label = statusLabel(row.status);
                      const tone = { good: 'text-emerald-400/80', bad: 'text-red-400/80', warn: 'text-amber-400/80', muted: 'text-white/40' }[label.tone];
                      const when = row.executed_at || row.decided_at || row.proposed_at;
                      return (
                        <li key={row.action_id} className="text-sm text-white/70">
                          <div className="flex items-center gap-2">
                            <History className="w-3.5 h-3.5 shrink-0 text-white/30" />
                            <span className="font-medium">{actionKind(row.action_type)}</span>
                            <span className={`ml-auto text-xs ${tone}`}>{label.text}</span>
                          </div>
                          <p className="m-0 mt-0.5 ml-5 text-xs text-white/40 truncate">{actionSummary(row)}</p>
                          <p className="m-0 ml-5 text-[11px] text-white/25">
                            {when ? new Date(when).toLocaleString() : ''}
                            {row.decided_at && row.status !== 'awaiting_approval' ? ` · decided ${new Date(row.decided_at).toLocaleTimeString()}` : ''}
                          </p>
                          {row.error && <p className="m-0 ml-5 text-[11px] text-red-300/60">{row.error}</p>}
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>
            </section>

            {/* Google */}
            <section className="mb-5">
              <p className="text-[11px] font-semibold uppercase tracking-widest text-white/30 mb-2">Google</p>
              <div className="rounded-xl bg-white/[0.03] border border-white/[0.06] p-3">
                <ul className="list-none m-0 p-0 space-y-2 mb-3">
                  {SERVICES.map(({ key, label, icon: Icon }) => {
                    const on = googleConnected && googleServices?.[key];
                    return (
                      <li key={key} className="flex items-center gap-2.5 text-sm text-white/60">
                        <Icon className="w-4 h-4" />
                        <span>{label}</span>
                        <span className={`ml-auto text-xs ${on ? 'text-emerald-400/80' : googleConnected ? 'text-amber-400/80' : 'text-red-400/70'}`}>
                          {on ? 'Allowed' : googleConnected ? 'Reconnect to allow' : 'Not connected'}
                        </span>
                      </li>
                    );
                  })}
                </ul>
                <div className="flex gap-2">
                  <button onClick={connect} disabled={busy}
                    className="flex-1 inline-flex items-center justify-center gap-2 rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm text-white/80 hover:bg-white/10 cursor-pointer disabled:opacity-50">
                    <Link2 className="w-4 h-4" />
                    {googleConnected ? 'Reconnect' : 'Connect Google'}
                  </button>
                  {googleConnected && (
                    <button onClick={disconnect} disabled={busy}
                      className="inline-flex items-center justify-center gap-2 rounded-lg border border-red-400/20 bg-red-500/5 px-3 py-2 text-sm text-red-300/80 hover:bg-red-500/10 cursor-pointer disabled:opacity-50">
                      <Unlink className="w-4 h-4" />
                      Disconnect
                    </button>
                  )}
                </div>
              </div>
            </section>

            {/* Account: verification, export, deletion */}
            <section className="mb-5">
              <p className="text-[11px] font-semibold uppercase tracking-widest text-white/30 mb-2">Your data</p>
              <div className="rounded-xl bg-white/[0.03] border border-white/[0.06] p-3 space-y-3">
                <div className="flex items-center gap-2.5 text-sm text-white/60">
                  <ShieldCheck className="w-4 h-4" />
                  <span>Email address</span>
                  {user?.email_verified ? (
                    <span className="ml-auto text-xs text-emerald-400/80">Confirmed</span>
                  ) : (
                    <button onClick={sendVerification} disabled={accountBusy}
                      className="ml-auto text-xs text-amber-300/90 underline bg-transparent border-none cursor-pointer disabled:opacity-50">
                      Not confirmed: send the link again
                    </button>
                  )}
                </div>
                <button onClick={downloadData} disabled={accountBusy}
                  className="w-full inline-flex items-center justify-center gap-2 rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm text-white/80 hover:bg-white/10 cursor-pointer disabled:opacity-50">
                  <Download className="w-4 h-4" /> Download all my data (ZIP)
                </button>
                <p className="m-0 text-xs text-white/30">Your account details, reminders, tasks, activity, conversations and the documents you uploaded. Passwords and Google tokens are never included.</p>

                {!confirmDelete ? (
                  <button onClick={() => setConfirmDelete(true)} disabled={accountBusy}
                    className="w-full inline-flex items-center justify-center gap-2 rounded-lg border border-red-400/20 bg-red-500/5 px-3 py-2 text-sm text-red-300/80 hover:bg-red-500/10 cursor-pointer disabled:opacity-50">
                    <Trash2 className="w-4 h-4" /> Delete my account…
                  </button>
                ) : (
                  <div className="rounded-lg border border-red-400/30 bg-red-500/5 p-3 space-y-2">
                    <p className="m-0 text-xs text-red-200/80">
                      This permanently deletes your account and everything in it: documents, reminders, tasks, conversations and
                      history. It also cancels the app's access to your Google account. It cannot be undone.
                    </p>
                    <input type="password" value={deleteProof} onChange={(event) => setDeleteProof(event.target.value)}
                      aria-label={user?.has_password === false ? 'Your email address' : 'Your password'}
                      placeholder={user?.has_password === false ? 'Type your email address to confirm' : 'Enter your password to confirm'}
                      autoComplete="current-password"
                      className="w-full rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-sm text-white outline-none" />
                    <div className="flex gap-2">
                      <button onClick={() => { setConfirmDelete(false); setDeleteProof(''); }} disabled={accountBusy}
                        className="flex-1 rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm text-white/70 cursor-pointer disabled:opacity-50">
                        Cancel
                      </button>
                      <button onClick={deleteAccount} disabled={accountBusy || !deleteProof}
                        className="flex-1 rounded-lg border border-red-400/40 bg-red-500/20 px-3 py-2 text-sm font-semibold text-red-100 cursor-pointer disabled:opacity-50">
                        Delete everything
                      </button>
                    </div>
                  </div>
                )}
              </div>
            </section>

            {/* Local data */}
            <section className="mb-6">
              <p className="text-[11px] font-semibold uppercase tracking-widest text-white/30 mb-2">This browser</p>
              <button
                onClick={() => (confirmClear ? clearHistory() : setConfirmClear(true))}
                className={`w-full inline-flex items-center justify-center gap-2 rounded-lg border px-3 py-2 text-sm cursor-pointer ${
                  confirmClear ? 'border-red-400/40 bg-red-500/10 text-red-300' : 'border-white/10 bg-white/5 text-white/70 hover:bg-white/10'
                }`}
              >
                <Trash2 className="w-4 h-4" />
                {confirmClear ? 'Click again to clear the conversation list' : 'Clear conversation list'}
              </button>
              <p className="mt-1.5 mb-0 text-xs text-white/30">Removes the chat list saved in this browser. Your reminders, tasks and documents are kept.</p>
            </section>

            <button onClick={signOut}
              className="w-full inline-flex items-center justify-center gap-2 rounded-lg bg-gradient-to-r from-[#00f0ff]/90 to-[#3b82f6]/90 px-3 py-2.5 text-sm font-semibold text-[#0a0a0a] cursor-pointer border-none">
              <LogOut className="w-4 h-4" />
              Sign out
            </button>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
