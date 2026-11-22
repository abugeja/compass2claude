import { test } from "node:test";
import assert from "node:assert/strict";
import worker, { checkUrl, scrub, looksLikeLogin, keysMatch, markdownFrom, allowedHosts } from "../src/index.js";

const hosts = allowedHosts({});
const KEY = "test-key-123";

function req(body, key = KEY, method = "POST") {
  return new Request("https://w.example/", {
    method,
    headers: { authorization: `Bearer ${key}`, "content-type": "application/json" },
    body: method === "POST" ? JSON.stringify(body) : undefined,
  });
}

function env(quickAction) {
  return { FETCH_KEY: KEY, BROWSER: { quickAction } };
}

test("allowlist", () => {
  assert.ok(checkUrl("https://newsletters.naavi.com/i/x/page/1", hosts).ok);
  assert.ok(checkUrl("https://williamstownps-vic.compass.education/Communicate/News/A.aspx", hosts).ok);
  assert.equal(checkUrl("http://newsletters.naavi.com/", hosts).error, "https only");
  assert.equal(checkUrl("https://evil.com/", hosts).error, "host not allowed");
  assert.equal(checkUrl("https://newsletters.naavi.com.evil.com/", hosts).error, "host not allowed");
  assert.equal(checkUrl("https://u:p@newsletters.naavi.com/", hosts).error, "credentials in url");
  assert.equal(checkUrl("nope", hosts).error, "invalid url");
});

test("scrub removes compass links and tokens", () => {
  const md = "See [item](https://williamstownps-vic.compass.education/Feed/FeedItem.aspx?accessToken=abc123&x=1) and accessToken=zzz9";
  const out = scrub(md);
  assert.ok(!out.includes("compass.education"));
  assert.ok(!out.includes("abc123"));
  assert.ok(!out.includes("zzz9"));
  assert.equal(scrub("https://newsletters.naavi.com/i/x"), "https://newsletters.naavi.com/i/x");
});

test("login detection only for compass", () => {
  assert.ok(looksLikeLogin("Username\nPassword\nSign in", "williamstownps-vic.compass.education"));
  assert.ok(!looksLikeLogin("Username\nPassword", "newsletters.naavi.com"));
  assert.ok(!looksLikeLogin("Dear Families, link below", "williamstownps-vic.compass.education"));
});

test("key compare", () => {
  assert.ok(keysMatch(KEY, KEY));
  assert.ok(!keysMatch("test-key-124", KEY));
  assert.ok(!keysMatch(KEY, undefined));
  assert.ok(!keysMatch("", KEY));
});

test("markdownFrom shapes", async () => {
  assert.equal(await markdownFrom(new Response(JSON.stringify({ success: true, result: "# Hi" }))), "# Hi");
  assert.equal(await markdownFrom(new Response("# Plain")), "# Plain");
  assert.equal(await markdownFrom({ success: true, result: "# Obj" }), "# Obj");
  await assert.rejects(markdownFrom({ success: false }));
});

test("handler: auth, method, host", async () => {
  const e = env(async () => assert.fail("should not render"));
  assert.equal((await worker.fetch(req({ url: "https://newsletters.naavi.com/" }, "wrong"), e)).status, 401);
  assert.equal((await worker.fetch(req(null, KEY, "GET"), e)).status, 405);
  assert.equal((await worker.fetch(req({ url: "https://example.com/" }), e)).status, 400);
});

test("handler: markdown is scrubbed", async () => {
  const e = env(async (action, opts) => {
    assert.equal(action, "markdown");
    return new Response(JSON.stringify({ success: true, result: `Link https://williamstownps-vic.compass.education/x?accessToken=SECRET1 and ${opts.url}` }));
  });
  const r = await worker.fetch(req({ url: "https://newsletters.naavi.com/i/x/page/2" }), e);
  assert.equal(r.status, 200);
  const { markdown } = await r.json();
  assert.ok(!markdown.includes("SECRET1"));
  assert.ok(markdown.includes("newsletters.naavi.com/i/x/page/2"));
});

test("handler: compass login page -> 403", async () => {
  const e = env(async () => new Response(JSON.stringify({ success: true, result: "Sign in to Compass\nUsername\nPassword\nForgot password" })));
  const r = await worker.fetch(req({ url: "https://williamstownps-vic.compass.education/Communicate/News/A.aspx?accessToken=T" }), e);
  assert.equal(r.status, 403);
  assert.equal((await r.json()).error, "login_required");
});

test("handler: render error does not echo the url", async () => {
  const e = env(async () => { throw new Error("timeout"); });
  const r = await worker.fetch(req({ url: "https://williamstownps-vic.compass.education/x?accessToken=LEAKME" }), e);
  assert.equal(r.status, 502);
  assert.ok(!(await r.text()).includes("LEAKME"));
});

test("handler: screenshot passthrough", async () => {
  const png = new Uint8Array([137, 80, 78, 71]);
  const e = env(async (action) => { assert.equal(action, "screenshot"); return new Response(png); });
  const r = await worker.fetch(req({ url: "https://newsletters.naavi.com/i/x/page/14", format: "screenshot" }), e);
  assert.equal(r.headers.get("content-type"), "image/png");
  assert.deepEqual(new Uint8Array(await r.arrayBuffer()), png);
});
