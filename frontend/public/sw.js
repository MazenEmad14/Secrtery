/* Service worker for Web Push: shows the notification and opens the app when it is clicked. */
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));

self.addEventListener('push', (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch { /* not JSON: show the generic title */ }
  const title = data.title || 'NexusAI';
  event.waitUntil(self.registration.showNotification(title, {
    body: data.body || '',
    icon: '/favicon.svg',
    tag: data.id || undefined,
    data: { id: data.id || null },
  }));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    if (windows.length) {
      await windows[0].focus();
    } else {
      await self.clients.openWindow('/');
    }
  })());
});
