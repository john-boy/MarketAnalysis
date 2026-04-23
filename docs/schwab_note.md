# Schwab API Account

The APP name is greenthread_market_analysis.

The APP machine name is prod-jhenrygreenthreadnet-0c310086-2b7e-4f61-a138-a8383cb50b74

## Key findings

(The main doc returned 403 on direct fetch, but the search snippets and `schwab-py`'s notes cover it.)

### What Schwab requires

1. **HTTPS only.** Plain `http://` is rejected — even for loopback. This is unusual (Google/Microsoft allow `http://127.0.0.1` for native-app loopback); Schwab doesn't.
2. **A port is mandatory in practice.** No port = port 443, and your OS will refuse to bind a non-root process to it. The community standard is **`https://127.0.0.1:8182`**.
3. **Exact-match required.** Case-sensitive, trailing-slash-sensitive. `https://127.0.0.1:8182` and `https://127.0.0.1:8182/` are *different* values. The registered URL in the developer portal must match **exactly** what the app sends as `redirect_uri`.
4. **30-second window.** Schwab expects the callback server to respond within 30s of the user completing authorization.
5. **255-character limit** for the callback-URL field (total, including multiple URLs if listed).

### Implications for Market Analysis

- Our `config/settings.toml` value **`https://127.0.0.1:8182` is correct** — keep it.
- Because CAs don't issue certs for loopback addresses, we'll need a **self-signed cert** generated once and trusted locally. The OAuth callback server runs only during token acquisition, not continuously.
- The local callback server is a **one-shot**: start → open browser to Schwab auth URL → receive redirect with `code` → exchange for tokens → shut down. After that, refresh tokens carry you for 7 days each (Schwab's refresh-token TTL), re-prompting the user when expired.

### Recommendation: don't hand-roll this

The mechanics (self-signed cert, transient HTTPS server, strict URI matching, refresh lifecycle) are fiddly and easy to get subtly wrong. Two sensible paths:

- **Use `schwab-py`** (`alexgolec/schwab-py`) — handles cert generation, callback server, token persistence, and refresh. Mature, maintained, and targets this exact flow. I'd lean this way.
- **Hand-roll** with `cryptography` (cert) + `http.server` (callback) + `requests-oauthlib` (token exchange) — 150–200 lines. Only worth it if we want zero third-party Schwab code.

For Phase 4, my plan is `schwab-py` as the auth + HTTP layer, wrapped by our own thin adapter in `sources/schwab.py` so UI/services see a typed API and can be swapped later.

### Two gotchas to record now

Adding to the plan's Phase 4 checklist:

1. **When you register the app**, copy the redirect URI we use (`https://127.0.0.1:8182` — no trailing slash) into the Schwab portal **exactly**. The #1 OAuth failure reported on forums is a trailing-slash mismatch.
2. **Self-signed cert must be trusted locally** (macOS Keychain or equivalent) once, so the browser doesn't block the redirect. `schwab-py` auto-generates one on first run.

Want me to fold these into `docs/plan.md` Phase 4 now, so they're captured before we get there?

Sources:
- [App Callback URL Requirements (Schwab)](https://developer.schwab.com/user-guides/apis-and-apps/app-callback-url-requirements)
- [Authenticate with OAuth (Schwab)](https://developer.schwab.com/user-guides/get-started/authenticate-with-oauth)
- [schwab-py authentication docs](https://schwab-py.readthedocs.io/en/latest/auth.html)
- [alexgolec/schwab-py on GitHub](https://github.com/alexgolec/schwab-py)

