import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { CheckCircle2, XCircle, Loader2 } from 'lucide-react';

import { verifyEmail } from '../services/api';

/** Where the link in the verification email lands: /verify-email?token=... The token is sent once, by POST. */
export default function VerifyEmailPage() {
  const [params] = useSearchParams();
  const token = params.get('token') || '';
  const [state, setState] = useState(token ? 'checking' : 'missing'); // checking | done | failed | missing
  const [message, setMessage] = useState('');

  useEffect(() => {
    if (!token) return undefined;
    let cancelled = false;
    verifyEmail(token).then(
      () => { if (!cancelled) setState('done'); },
      (err) => { if (!cancelled) { setMessage(err?.message || ''); setState('failed'); } },
    );
    return () => { cancelled = true; };
  }, [token]);

  return (
    <div className="min-h-screen bg-[#0a0a0a] flex items-center justify-center px-4 text-white">
      <div className="w-full max-w-sm rounded-2xl border border-white/10 bg-[#111] p-8 text-center">
        {state === 'checking' && (
          <>
            <Loader2 className="mx-auto mb-4 h-8 w-8 animate-spin text-white/50" />
            <p className="m-0 text-sm text-white/60">Confirming your email address…</p>
          </>
        )}
        {state === 'done' && (
          <>
            <CheckCircle2 className="mx-auto mb-4 h-10 w-10 text-emerald-400" />
            <h1 className="m-0 mb-2 text-lg font-semibold">Email confirmed</h1>
            <p className="m-0 mb-6 text-sm text-white/50">Thank you. Your address is verified.</p>
            <Link to="/workspace" className="inline-block rounded-lg bg-white/10 px-4 py-2 text-sm text-white no-underline hover:bg-white/15">
              Continue to NexusAI
            </Link>
          </>
        )}
        {(state === 'failed' || state === 'missing') && (
          <>
            <XCircle className="mx-auto mb-4 h-10 w-10 text-red-400" />
            <h1 className="m-0 mb-2 text-lg font-semibold">This link did not work</h1>
            <p className="m-0 mb-6 text-sm text-white/50">
              {state === 'missing' ? 'The link is incomplete.' : message || 'It may have expired or already been used.'}
              {' '}Sign in and ask for a new one in Settings.
            </p>
            <Link to="/workspace" className="inline-block rounded-lg bg-white/10 px-4 py-2 text-sm text-white no-underline hover:bg-white/15">
              Go to NexusAI
            </Link>
          </>
        )}
      </div>
    </div>
  );
}
