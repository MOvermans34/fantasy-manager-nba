// Minimale service worker: maakt de app installeerbaar; data komt altijd live van de server.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => { });
