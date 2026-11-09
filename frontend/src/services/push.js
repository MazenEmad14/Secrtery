/**
 * Web Push in the browser: register the service worker, ask permission, subscribe, and tell the server.
 * Everything here needs a browser with PushManager, and HTTPS (localhost counts as secure).
 */
import { getPushPublicKey, removePushSubscription, savePushSubscription } from './api';
import { urlBase64ToUint8Array } from './pushKey';

export function pushSupported() {
  return typeof window !== 'undefined'
    && 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
}

async function registration() {
  await navigator.serviceWorker.register('/sw.js');
  return navigator.serviceWorker.ready;
}

/** Subscribe this browser. Throws an Error with a readable message when it cannot. */
export async function enablePush() {
  if (!pushSupported()) throw new Error('This browser does not support push notifications.');
  const key = await getPushPublicKey();
  if (!key) throw new Error('Push notifications are not set up on this server.');
  if ((await Notification.requestPermission()) !== 'granted') {
    throw new Error('Notifications are blocked for this site. Allow them in the browser settings, then try again.');
  }
  const reg = await registration();
  const existing = await reg.pushManager.getSubscription();
  const subscription = existing || await reg.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: urlBase64ToUint8Array(key),
  });
  await savePushSubscription(subscription);
}

/** Unsubscribe this browser (other browsers of the same user are not affected). */
export async function disablePush() {
  if (!pushSupported()) return;
  const reg = await navigator.serviceWorker.getRegistration('/sw.js');
  const subscription = reg ? await reg.pushManager.getSubscription() : null;
  if (!subscription) return;
  const { endpoint } = subscription;
  await subscription.unsubscribe();
  await removePushSubscription(endpoint);
}

/** Is this browser currently subscribed? */
export async function browserIsSubscribed() {
  if (!pushSupported()) return false;
  const reg = await navigator.serviceWorker.getRegistration('/sw.js');
  return Boolean(reg && await reg.pushManager.getSubscription());
}
