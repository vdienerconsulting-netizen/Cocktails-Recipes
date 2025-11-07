const CACHE_NAME = 'cocktails-v1';
const urlsToCache = ['/', '/index.html', '/manifest.json'];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(urlsToCache))
  );
});

self.addEventListener('fetch', event => {
  event.respondWith(
    caches.match(event.request).then(response => response || fetch(event.request))
  );
});
```

### **2. Créer un nouveau service Render**

1. Allez sur https://dashboard.render.com
2. Cliquez **"New +"** > **"Static Site"**
3. Connectez votre repository GitHub
4. **Build Command** : Laissez vide
5. **Publish Directory** : `.` (juste un point)
6. Cliquez **"Create Static Site"**

### **3. C'est terminé !**

Votre app sera accessible à une URL comme :
```
https://votre-nom.onrender.com
