// Service Worker — ASI Biont PWA
// Push notifications + offline shell

self.addEventListener('install', function(event) {
  self.skipWaiting();
});

self.addEventListener('activate', function(event) {
  event.waitUntil(clients.claim());
});

// Push notification handler
self.addEventListener('push', function(event) {
  var data = {};
  try { data = event.data ? event.data.json() : {}; } catch(e) { data = { body: event.data ? event.data.text() : '' }; }
  var title = data.title || 'ASI Biont';
  var options = {
    body: data.body || '',
    icon: '/static/favicon.ico',
    badge: '/static/favicon.ico',
    tag: data.tag || 'asibiont-' + Date.now(),
    data: { url: data.url || '/dashboard' },
    vibrate: [200, 100, 200]
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

// Click on notification — open dashboard
self.addEventListener('notificationclick', function(event) {
  event.notification.close();
  var url = event.notification.data && event.notification.data.url ? event.notification.data.url : '/dashboard';
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function(clientList) {
      for (var i = 0; i < clientList.length; i++) {
        if (clientList[i].url.indexOf('/dashboard') !== -1 && 'focus' in clientList[i]) {
          return clientList[i].focus();
        }
      }
      return clients.openWindow(url);
    })
  );
});
