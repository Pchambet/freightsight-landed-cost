/**
 * A shared report's token lives in the path of its page (`/r/<token>`) — that is what a link is —
 * and it is the whole credential. It must not travel anywhere else: error and performance events
 * carry URLs (the request, the transaction name, navigation breadcrumbs, span descriptions), so
 * every event is rewritten before it leaves. Working on the serialized event catches a URL wherever
 * the SDK put it, including places a later SDK version may add.
 */
const TOKEN_IN_PATH = /\/r\/[A-Za-z0-9_-]{16,}/g;

export function scrubShareTokens<T>(event: T): T {
  try {
    const raw = JSON.stringify(event);
    if (!raw.includes("/r/")) return event;
    return JSON.parse(raw.replace(TOKEN_IN_PATH, "/r/[token]")) as T;
  } catch {
    return event;
  }
}
