/* 서비스워커 — 껍데기(HTML·아이콘)만 캐시해서 오프라인에서도 화면이 뜨게 한다.
   /api/* 는 절대 캐시하지 않는다: 작업 상태를 캐시하면 진행률이 멈춘 것처럼 보인다. */

const CACHE = "ytaudio-v1";
const SHELL = ["/", "/manifest.webmanifest", "/icon-192.png", "/icon-512.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  //버전 올렸을 때 옛 캐시 제거
  e.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.pathname.startsWith("/api/")) return;

  //껍데기는 네트워크 우선, 실패 시 캐시 — 서버가 꺼져 있어도 화면은 뜬다
  e.respondWith(
    fetch(e.request)
      .then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(e.request, copy));
        return res;
      })
      .catch(() => caches.match(e.request).then((hit) => hit ?? caches.match("/")))
  );
});
