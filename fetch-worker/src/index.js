// compass2claude page fetcher.
//
// A small Worker that renders one school page in Cloudflare Browser Run and
// returns it as Markdown (or a PNG screenshot), so the cloud digest task can
// read "News:" items without a local browser.
//
// Guardrails:
//   - Bearer key required (FETCH_KEY secret), compared in constant time.
//   - Only allowlisted school hosts can be fetched (ALLOWED_HOSTS var).
//   - Compass links and access tokens are stripped from every Markdown reply,
//     so a parent's login token can never reach an outbox file.
//   - Nothing about the request URL is logged.

const DEFAULT_HOSTS =
  "newsletters.naavi.com,williamstownps-vic.compass.education,williamstownps.vic.edu.au,www.williamstownps.vic.edu.au";

const COMPASS_RE = /https?:\/\/[^\s)\]>"']*compass\.education[^\s)\]>"']*/gi;
const TOKEN_RE = /accessToken=[A-Za-z0-9_\-.]+/gi;
const LOGIN_RE = /(forgot (your )?password|sign in to compass|log ?in to compass|username[\s\S]{0,80}password)/i;

export function allowedHosts(env) {
  return (env.ALLOWED_HOSTS || DEFAULT_HOSTS)
    .split(",")
    .map((h) => h.trim().toLowerCase())
    .filter(Boolean);
}

export function checkUrl(raw, hosts) {
  let u;
  try {
    u = new URL(raw);
  } catch {
    return { ok: false, error: "invalid url" };
  }
  if (u.protocol !== "https:") return { ok: false, error: "https only" };
  if (u.username || u.password) return { ok: false, error: "credentials in url" };
  if (!hosts.includes(u.hostname.toLowerCase())) return { ok: false, error: "host not allowed" };
  return { ok: true, url: u };
}

// Outlook "safelinks" wrap the real URL and leak the sender's email address.
const SAFELINK_RE = /https:\/\/[a-z0-9.-]*safelinks\.protection\.outlook\.com\/\?[^\s)\]>"']*/gi;

export function unwrapSafelinks(md) {
  return md.replace(SAFELINK_RE, (link) => {
    try {
      const real = new URL(link).searchParams.get("url");
      return real && /^https?:\/\//i.test(real) ? real : "[link removed]";
    } catch {
      return "[link removed]";
    }
  });
}

export function scrub(md) {
  return unwrapSafelinks(md)
    .replace(COMPASS_RE, "[compass link removed]")
    .replace(TOKEN_RE, "accessToken=[removed]");
}

export function looksLikeLogin(md, host) {
  return host.endsWith("compass.education") && LOGIN_RE.test(md);
}

export function keysMatch(given, expected) {
  if (!expected || typeof given !== "string") return false;
  const a = new TextEncoder().encode(given);
  const b = new TextEncoder().encode(expected);
  let diff = a.length ^ b.length;
  for (let i = 0; i < Math.max(a.length, b.length); i++) diff |= (a[i] || 0) ^ (b[i] || 0);
  return diff === 0;
}

// quickAction may return a Response or a plain value; the REST-style body is
// {success, result}. Accept any of these shapes.
export async function markdownFrom(res) {
  let body = res;
  if (res && typeof res.text === "function") body = await res.text();
  if (typeof body === "string") {
    try {
      body = JSON.parse(body);
    } catch {
      return body;
    }
  }
  if (body && typeof body === "object") {
    if (body.success === false) throw new Error("render failed");
    if (typeof body.result === "string") return body.result;
    if (body.result && typeof body.result.markdown === "string") return body.result.markdown;
  }
  return String(body ?? "");
}

function json(status, obj) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
  });
}

const GOTO = { waitUntil: "networkidle2", timeout: 45000 };

export default {
  async fetch(request, env) {
    if (request.method !== "POST") return json(405, { error: "POST only" });
    const auth = request.headers.get("authorization") || "";
    if (!keysMatch(auth.replace(/^Bearer\s+/i, ""), env.FETCH_KEY)) {
      return json(401, { error: "unauthorised" });
    }

    let input;
    try {
      input = await request.json();
    } catch {
      return json(400, { error: "body must be JSON" });
    }
    const check = checkUrl(input.url, allowedHosts(env));
    if (!check.ok) return json(400, { error: check.error });
    const host = check.url.hostname.toLowerCase();
    const format = input.format === "screenshot" ? "screenshot" : "markdown";

    try {
      if (format === "screenshot") {
        const shot = await env.BROWSER.quickAction("screenshot", {
          url: check.url.href,
          gotoOptions: GOTO,
          screenshotOptions: { fullPage: true, type: "png" },
          viewport: { width: 1100, height: 1400 },
        });
        const bytes = shot && typeof shot.arrayBuffer === "function" ? await shot.arrayBuffer() : shot;
        return new Response(bytes, {
          headers: { "content-type": "image/png", "cache-control": "no-store" },
        });
      }

      const res = await env.BROWSER.quickAction("markdown", {
        url: check.url.href,
        gotoOptions: GOTO,
      });
      const md = await markdownFrom(res);
      if (looksLikeLogin(md, host)) {
        return json(403, { error: "login_required" });
      }
      return json(200, { markdown: scrub(md) });
    } catch (e) {
      // Deliberately no URL in the error.
      return json(502, { error: "render failed", detail: String(e && e.message || e).slice(0, 200) });
    }
  },
};
