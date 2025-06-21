// sw.js - Service Worker yang Benar dan Fungsional

self.addEventListener('install', (event) => {
  console.log('Service Worker: Proses instalasi...');
  // Perintah ini memaksa service worker yang sedang menunggu untuk menjadi aktif.
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  console.log('Service Worker: Aktif.');
  // Mengambil kontrol halaman yang terbuka agar service worker bisa bekerja segera.
  clients.claim();
});

// Listener 'fetch' ini adalah syarat wajib agar aplikasi bisa di-instal.
// Strategi paling sederhana adalah langsung meneruskan permintaan ke jaringan.
self.addEventListener('fetch', (event) => {
  event.respondWith(fetch(event.request));
});