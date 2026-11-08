export interface Env {}

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);
    const startMs = Date.now();

    // Intercept NEXRAD Level III radar tile & telemetry requests for Station KBMX
    if (url.pathname.startsWith('/radar/tiles/') || url.pathname.startsWith('/api/v1/radar/')) {
      const cache = caches.default;
      const cacheKey = new Request(url.toString(), {
        method: 'GET',
        headers: request.headers,
      });

      // 1. Check Cloudflare global edge cache memory
      let response = await cache.match(cacheKey);
      if (response) {
        console.log(`[ForRad Edge] HIT | ${request.method} ${url.pathname} | In-Memory Cache | Duration: ${Date.now() - startMs}ms`);
        const hitHeaders = new Headers(response.headers);
        hitHeaders.set('X-ForRad-Edge-Cache', 'HIT');
        hitHeaders.set('X-ForRad-Station', 'KBMX');
        return new Response(response.body, {
          status: response.status,
          statusText: response.statusText,
          headers: hitHeaders,
        });
      }

      // 2. Cache MISS: Generate synthetic radar raster frame
      console.log(`[ForRad Edge] MISS | ${request.method} ${url.pathname} | Synthesizing Shield Frame | Duration: ${Date.now() - startMs}ms`);
      const mockRadarPayload = JSON.stringify({
        station: 'KBMX',
        product: 'NEXRAD_LEVEL_III',
        timestamp: Date.now(),
        status: 'OPERATIONAL',
        tile_url: url.pathname,
        mode: 'EDGE_SYNTHETIC_SHIELD',
      });

      const missHeaders = new Headers();
      missHeaders.set('Content-Type', 'application/json');
      missHeaders.set('Cache-Control', 'public, max-age=300, s-maxage=300');
      missHeaders.set('X-ForRad-Edge-Cache', 'MISS');
      missHeaders.set('X-ForRad-Station', 'KBMX');

      const cachedResponse = new Response(mockRadarPayload, {
        status: 200,
        headers: missHeaders,
      });

      if (request.method === 'GET') {
        ctx.waitUntil(cache.put(cacheKey, cachedResponse.clone()));
      }

      return cachedResponse;
    }

    console.log(`[ForRad Edge] PASSTHROUGH | ${request.method} ${url.pathname}`);
    return new Response('ForRad Edge Cache Active.', { status: 200 });
  },
};
