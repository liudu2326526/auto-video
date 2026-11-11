> Claude Code 2.1.310实际返回的只读提案；未读仓库、未调用工具。它不是已实现功能或法律判断。Codex负责审查和实施；现行接口/实证以material-providers.md为准。提案对Noto Apache的假设已被Google官方动画CC BY 4.0资料纠正。

{
  "status": "plan-only / read-only / no tools used / no files changed",
  "role": "architecture proposal for Codex review, not an implementation",
  "context": {
    "existing": "material_agent (stdlib): append-only SQLite record/blob/review/usage, sha256 dedupe, purpose gate, human review before handoff; Commons downloader with all-IP-public DNS check, TLS pin, robots, hop-by-hop same-host/same-name redirects, size/duration caps, MIME magic + FFmpeg decode, temp-file + transactional audit; default system DNS, optional explicit Cloudflare DoH pinned to fixed upload hostnames without fallback; runtime confined to var/, binaries/DB/secrets gitignored.",
    "goal_now": "Add a second-stage acquisition path: shot-scoped provider search (Pexels/Pixabay API) producing a small candidate set with frozen provenance, then an explicit single-candidate download into the existing content-addressed library. No key exists and none may be created or configured long-term, so the whole path must be exercised against mocks without ever claiming a real network success.",
    "invariants_that_must_not_move": [
      "record schema 1 field set is frozen; no extra keys injected anywhere",
      "no key in logs, DB rows, URL cache, Git, error strings, or anything sent to a media CDN or redirect target",
      "no adaptation of script, editing, or Gamma-shared files",
      "no robots bypass, no fetching past an access denial, no pagination crawling, no gallery/competitor mirroring",
      "human approval never inherited across candidate, provider, asset, license snapshot, or download URL",
      "provider query cache is disposable; the media library is durable; they are separate stores"
    ]
  },
  "module_layout": {
    "principle": "One new self-contained package under material_agent/prov/. Imports only public helpers from the existing package (blob store, secure-transport primitives, review/usage). Zero edits to existing modules and zero edits to schema 1.",
    "tree": [
      "material_agent/prov/__init__.py",
      "material_agent/prov/contracts.py      # contract ids + strict validators (no extra keys allowed)",
      "material_agent/prov/redact.py         # url/header/exception scrubbing; single choke point",
      "material_agent/prov/transport.py      # ApiRequest / PublicRequest, role enforcement, allowlists",
      "material_agent/prov/ratelimit.py      # header parse, 429 latch, provider pause state",
      "material_agent/prov/cache.py          # provider_query_cache (disposable), TTL, purge",
      "material_agent/prov/store.py          # candidate / license_snapshot / approval / provenance / download_attempt",
      "material_agent/prov/license_gate.py   # declarative registry, evidence hashes, blocked-by-default",
      "material_agent/prov/candidate.py      # state machine + approval token mint/consume",
      "material_agent/prov/ingest.py         # download -> verify -> dedupe -> provenance -> record_ref",
      "material_agent/prov/providers/pexels.py",
      "material_agent/prov/providers/pixabay.py",
      "material_agent/prov/providers/mixkit.py    # stub, always blocked until evidence",
      "material_agent/prov/providers/noto.py      # stub, blocked until per-item LICENSE + automation evidence",
      "material_agent/prov/cli.py             # python -m material_agent.prov.cli ...",
      "material_agent/prov/mocks/             # fixture api + fixture cdn servers, canned payloads, tiny real JPEG/MP4/GIF"
    ],
    "storage": {
      "var/prov/prov.sqlite3": "new DB: query cache (disposable), provider_state, candidate, license_snapshot, approval, provenance, download_attempt",
      "var/prov/license/": "manually captured license/terms snapshots (files + sha256), never fetched with credentials",
      "var/prov/approvals/": "single-use approval tokens, gitignored",
      "existing blob store": "media bytes only, content-addressed, reused for dedupe + handoff",
      "gitignore_additions": ["var/", "var/prov/", "*.sqlite3", "*.db", "fixtures with real keys never exist"]
    }
  },
  "transport_roles": {
    "rationale": "The leak that matters is a credential crossing the boundary from an authenticated API call into a credential-free media fetch. Enforce it in the type system, not in a reviewer's memory.",
    "api_role": {
      "type": "ApiRequest(provider, host, path, query, headers, auth=None)",
      "host_policy": "exact match against api_allowlist[provider]; no suffix matching, no wildcards",
      "redirect_policy": "DENY — any 3xx is a hard TransportDenied (exit 5); never followed, never retried",
      "auth_injection": {
        "pexels": "header Authorization: <key>, injected inside transport after the loggable form is built",
        "pixabay": "query param key=, injected last, and the loggable form is produced by redact.query() at construction time — the raw URL is never stored on the request object"
      },
      "hardening": ["no cookie jar", "ignore HTTP_PROXY/HTTPS_PROXY/ALL_PROXY env", "timeout 15s", "response body cap", "no request ids persisted that could encode a key"]
    },
    "cdn_role": {
      "type": "PublicRequest(host, path) — has no auth field at all, so a key is unrepresentable",
      "host_policy": "exact bare-host match against cdn_allowlist[provider]; an API-returned URL is a hint, never an authorization",
      "preconditions_reused_from_existing_downloader": ["all resolved IPs public", "TLS pin verified", "robots evaluated for our UA", "hop-by-hop: same host and same basename, max 3 hops", "size + duration caps", "MIME magic + FFmpeg decode on temp file"],
      "outgoing_guard": "assert outgoing header names ∩ {authorization, cookie, proxy-authorization, x-api-key} = ∅ and query param names ∩ provider.secret_param_names = ∅; any violation aborts before connect",
      "extra": ["https + port 443 only", "connect to the pinned IP with original Host/SNI", "ffmpeg constrained: -protocol_whitelist file, -v error, no network protocols"]
    },
    "license_doc_fetch": "Out of scope this phase. License/terms evidence is captured manually by a human and dropped into var/prov/license/ with a sha256; the tool only hashes and records. This keeps the role count at two."
  },
  "data_model": {
    "provider_query_cache": {
      "disposable": true,
      "columns": ["provider", "query_norm", "media_type", "orientation", "page", "request_fingerprint", "fetched_at", "expires_at", "http_status", "rate_json", "response_sanitized_path", "response_sha256"],
      "unique": "(provider, query_norm, media_type, orientation, page)",
      "notes": [
        "request_fingerprint = sha256 of the canonical request WITHOUT the key; results are key-independent so cross-key reuse is safe and stated explicitly in the docstring",
        "search JSON only; media bytes never enter the cache",
        "purge command deletes rows + files; candidates keep their own frozen snapshot so provenance survives a purge"
      ]
    },
    "provider_state": {
      "columns": ["provider", "paused_until", "last_http_status", "last_rate_json", "key_present", "applicable", "updated_at"],
      "notes": ["key_present is a boolean only", "applicable stays unknown for pexels until docs are re-verified; the tool must not assume an application path exists"]
    },
    "candidate": {
      "columns": ["candidate_id", "provider", "asset_id", "media_type", "query_text", "query_norm", "shot_id", "author_json", "source_page", "license_snapshot_sha256", "api_terms_snapshot_sha256", "download_hint_json", "source_cache_expires_at", "discovered_at", "expires_at", "state", "reason"],
      "idempotency": "candidate_id = sha256(provider|asset_id|query_norm|media_type)[:24]; re-searching the same shot does not duplicate"
    },
    "license_snapshot / approval / provenance / download_attempt": "own schema_version 1 each, strict validators reject unknown keys",
    "schema1_untouched": "The existing record gains no fields. Provenance points at it via record_ref stored on the provenance row, plus a sha256 join to the blob. If a future consumer needs the link forward, that is a schema 2 decision, not this phase."
  },
  "contracts": {
    "search.v1": {
      "request": {"shot_id": "S07", "query": "string", "media_type": "video|image", "orientation": "landscape|portrait|square|any", "limit": 8, "page": 1},
      "response": {
        "contract": "material_agent.provider.search.v1",
        "provider": "pexels|pixabay",
        "network": {"mode": "mock|live", "real": false},
        "cache": {"hit": false, "expires_at": "iso8601", "key_excluded": true},
        "rate": {"limit": 200, "remaining": 187, "reset_at": "iso8601"},
        "candidates": ["candidate.v1"],
        "truncated": true,
        "notes": ["limit clamped to provider max and to hard cap 20"]
      }
    },
    "candidate.v1": {
      "candidate_id": "cand_pexels_…",
      "provider": "pexels",
      "asset_id": "1234567",
      "media_type": "video",
      "animated": false,
      "author": {"name": "string", "profile_url": "string"},
      "source_page": "https://…",
      "query": {"text": "string", "normalized": "string", "ts": "iso8601", "scope": "shot:S07"},
      "license": {"id": "pexels-license", "url": "string", "snapshot_sha256": "string", "attribution_required": false, "captured_at": "iso8601"},
      "api_terms": {"id": "pexels-api", "url": "string", "snapshot_sha256": "string", "cache_policy": "verify", "automation_ok": "unknown"},
      "download_hint": {"cdn_host": "string", "path": "string", "allowed": false, "reason": "allowlist-not-confirmed"},
      "expires_at": "iso8601",
      "state": "discovered"
    },
    "approval.v1": {
      "candidate_id": "string",
      "actor": "string",
      "approved_at": "iso8601",
      "binds": {"asset_id": "string", "license_snapshot_sha256": "string", "api_terms_snapshot_sha256": "string", "source_page": "string", "download_host": "string"},
      "expires_at": "iso8601",
      "nonce": "string",
      "consumed_at": null,
      "rule": "single use; any field in binds changing invalidates the token and forces re-approval"
    },
    "ingest.v1": {
      "candidate_id": "string",
      "approval_verified": true,
      "transport": {"role": "cdn", "host": "string", "ip_set_sha256": "string", "tls_pin": "string", "redirects": 0, "robots": "allowed"},
      "verification": {"mime_sniffed": "video/mp4", "ffmpeg_decode": "ok", "bytes": 0, "duration_s": 0.0, "animated": false, "editing_compatible": false},
      "blob_sha256": "string",
      "deduped": false,
      "record_ref": {"kind": "material_agent.record.schema1", "id": "string"},
      "handoff": "pending_human_review"
    },
    "provenance.v1": {
      "contract": "material_agent.provider.provenance.v1",
      "candidate_id": "string",
      "blob_sha256": "string",
      "provider": "string",
      "asset_id": "string",
      "author": {"name": "string", "profile_url": "string"},
      "source_page": "string",
      "license_snapshot_sha256": "string",
      "api_terms_snapshot_sha256": "string",
      "query": {"text": "string", "ts": "iso8601", "scope": "shot:S07"},
      "search_fingerprint": "string",
      "approval": {"actor": "string", "at": "iso8601", "token_sha256": "string"},
      "transport": {"role": "cdn", "host": "string"},
      "record_ref": {"kind": "material_agent.record.schema1", "id": "string"},
      "created_at": "iso8601"
    },
    "error.v1": {
      "contract": "material_agent.provider.error.v1",
      "code": "TRANSPORT_REDIRECT_DENIED",
      "exit": 5,
      "provider": "pexels",
      "candidate_id": null,
      "message": "api redirect refused",
      "details": {"hop": 1},
      "key_present": true,
      "rule": "message and details are scrubbed before construction; no URL containing a secret may reach the error object"
    },
    "exit_codes": {"0": "ok", "2": "blocked (quota / license / provider paused)", "3": "needs human decision", "4": "unverified: no key or doc evidence missing", "5": "policy violation (redirect, host, key in URL/header)", "6": "internal"}
  },
  "cli": {
    "commands": [
      "prov doctor [--provider X]                # key_present bool, paused state, license evidence status, allowlist state; never prints a key",
      "prov search --provider X --shot-id S07 --query '…' --type video --limit 8 [--cache-only] [--json]",
      "prov show --candidate ID",
      "prov select --candidate ID --actor NAME",
      "prov approve --candidate ID --license-snapshot SHA --actor NAME   # mints single-use token in var/prov/approvals/",
      "prov ingest --candidate ID --approval-token PATH                  # download + verify + dedupe + provenance",
      "prov reject --candidate ID --actor NAME --reason '…'",
      "prov cache stats|purge [--provider X]",
      "prov state resume --provider X --note '…'                        # the only way out of a 429/403 latch",
      "prov license status|snapshot-hash --file PATH --provider X",
      "prov mock run --scenario mocks/scenarios/<name>.json"
    ],
    "rules": [
      "no command accepts a key on argv (argv is visible in ps); the key is read from the provider's env var at call time only",
      "no command ever writes the key to disk, DB, cache, or stdout",
      "every command emits one JSON object on stdout when --json is given"
    ]
  },
  "state_machine": {
    "states": ["discovered", "shortlisted", "approved", "ingested", "handed_off", "expired", "rejected", "blocked_license"],
    "transitions": [
      "discovered -> shortlisted  (explicit select)",
      "shortlisted -> approved     (human approve; requires fresh license snapshot hash + unexpired candidate)",
      "approved -> ingested        (approval token consumed; second use rejected)",
      "ingested -> handed_off      (existing review/usage path; human review still gates handoff)",
      "discovered|shortlisted -> expired          (candidate TTL, default 24h)",
      "any pre-ingest -> blocked_license          (license or api_terms snapshot changed)",
      "discovered|shortlisted|approved -> rejected (human, with reason)"
    ],
    "non_inheritance": [
      "approval is bound to (candidate_id, asset_id, license_snapshot_sha256, api_terms_snapshot_sha256, source_page, download_host, expiry)",
      "re-approval is mandatory if any bound field changes, if the candidate expired, or if a re-check returns different license text",
      "pre-ingest re-check: license snapshot must still be within its freshness window and byte-identical; otherwise exit 2, not a warning"
    ]
  },
  "cache_and_quota": {
    "cache_key": "provider + query_norm + media_type + (+orientation) + page; the key material is deliberately excluded and this is documented as safe because results are key-independent",
    "ttl": {"pixabay": 86400, "pexels": 86400, "basis": "pixabay documented 24h query caching and pexels recommended 24h are both recorded as doc claims to re-verify; the architect treats 24h as the floor and makes the value a per-provider config with a written source reference", "stale_reads": "expired rows are readable only with --allow-stale for offline review and are never auto-refreshed in the background"},
    "rate_headers": {"store": "limit / remaining / reset as opaque numbers in provider_state and the cache row", "use": "informational only; never auto-sleep, never auto-retry, never auto-rotate keys"},
    "429": "single attempt; latch provider_state.paused_until from Retry-After when present, otherwise indefinite; exit 2; only prov state resume clears it",
    "403_451": "latch as blocked, exit 2, no workaround attempts",
    "enumeration_limits": ["max_pages = 1 (hard)", "per_page <= provider documented max", "max_candidates_per_query = 8 default, 20 hard", "each search must carry a shot_id; free-form library building is rejected by the CLI", "no query-variant sweeps, no result-set mirroring, no storing of non-selected media"]
  },
  "license_gates": {
    "registry": "material_agent/prov/license_registry.json shipped with every entry verified=false, snapshot_sha256=null, allowed_automation=unknown",
    "gate_rule": "ingest requires allowed_automation == true AND a non-expired snapshot (< 30d) AND a human-captured evidence file; anything else exits 2 with blocked_license",
    "per_provider": {
      "pexels": "license page + API terms captured; automation permitted only insofar as the API terms say; cache policy recorded verbatim",
      "pixabay": "license/terms captured; the 24h cache clause copied verbatim into the registry rather than paraphrased",
      "mixkit": "stays blocked; requires per-item license confirmation and an explicit statement that automated download is permitted",
      "noto": "candidate evidence is the Apache-2.0 repo LICENSE plus per-item metadata proving the animation asset itself is covered; until both are captured with hashes, blocked. Do not infer 'only icons' or 'only stills' from the repo license alone"
    },
    "google_login": "No service is specified, so no OAuth implementation, no credential storage, no scope expansion, no token file. The statement 'the user allows Google login' is recorded as context only and grants nothing."
  },
  "media_rules": {
    "allowed_containers_phase_1": ["image/jpeg", "video/mp4"],
    "gif": "media_type 'image/gif' must be verified as animated (frame count > 1) and tagged animated=true, editing_compatible=false. The existing decoder takes the first frame only, so the pipeline must never present a GIF as a still image nor claim GIF editing compatibility.",
    "classification_order": ["magic bytes", "container probe", "ffmpeg decode", "frame/animation check for gif"],
    "cdn_trust": "an API-returned URL is a hint. allowed=false until the host is in the exact cdn_allowlist for that provider and every other precondition passes."
  },
  "mock_plan": {
    "servers": "two stdlib http.server origins inside prov/mocks/: fixture-api (canned JSON, scripts rate headers, can be told to return 429 / 302 / an absolute CDN URL / an echoing endpoint) and fixture-cdn (serves tiny real JPEG, MP4, and an animated GIF, and records every received header)",
    "addressing": "transport in mock mode resolves fixture hostnames to 127.0.0.1 through an explicit hosts-override table in config, not /etc/hosts, so the allowlist logic is exercised unchanged",
    "scenarios": [
      "search_hit -> show -> select -> approve -> ingest (JPEG)",
      "search_hit -> ingest (MP4)",
      "search_cache_hit (second search within TTL performs no request)",
      "search_expired_cache (re-fetch counted, headers recorded)",
      "ratelimit_429 (latch, exit 2, second call performs no request)",
      "api_redirect_302 (exit 5, no follow)",
      "api_returns_foreign_cdn_host (download_hint.allowed=false, ingest exits 5)",
      "key_echo_attempt (fixture-cdn asserts Authorization absent and no secret-shaped query param)",
      "gif_animated (animated=true, editing_compatible=false)",
      "license_snapshot_changed (approval invalidated, exit 2)",
      "expired_candidate_ingest (exit 3)",
      "approval_token_replay (second ingest rejected)"
    ],
    "honesty_rule": "every mock response carries network.real=false and the CLI prints mode=mock; no code path claims a live fetch without a real HTTP status"
  },
  "threats": [
    {"id": "T1", "threat": "Key leaks through exception text or a URL string (urllib embeds the full URL, which for pixabay contains the key)", "mitigation": "transport catches at its boundary and builds errors from a pre-scrubbed loggable form; redact.py is the only place a URL becomes a string"},
    {"id": "T2", "threat": "Key follows an API redirect or an API-supplied absolute URL to a media host", "mitigation": "api role denies redirects outright; cdn role has no auth field; host exact-match; secret-shaped query params rejected"},
    {"id": "T3", "threat": "SSRF via API-returned URL (private IP, metadata endpoint, DNS rebinding)", "mitigation": "reuse all-IPs-public check, pin the IP, ignore proxy env vars"},
    {"id": "T4", "threat": "Stale license approved after the terms changed", "mitigation": "snapshot hash bound into the approval; re-check immediately before ingest; TTL on the snapshot"},
    {"id": "T5", "threat": "Approval replayed onto a different candidate or a re-encoded asset", "mitigation": "single-use nonce, binds asset_id + hashes + host"},
    {"id": "T6", "threat": "Quota exhaustion or provider ban from retry loops or pagination crawling", "mitigation": "429 latch, no retries, max_pages=1, per-shot caps, no variant sweeps"},
    {"id": "T7", "threat": "Terms violation by bulk harvesting or mirroring a gallery", "mitigation": "shot_id required, small fixed candidate cap, media fetched only for the single approved candidate"},
    {"id": "T8", "threat": "MIME/container deception or a hostile media file reaching FFmpeg", "mitigation": "magic bytes + constrained ffmpeg (-protocol_whitelist file), size/duration caps, temp file, transactional audit"},
    {"id": "T9", "threat": "Cache rows become an accidental second media library or leak query text into Git", "mitigation": "cache marked disposable, purge command, DB under var/, gitignore verified by test"},
    {"id": "T10", "threat": "GIF misclassified as a still and silently truncated to frame 1", "mitigation": "frame-count check, animated flag, editing_compatible=false, refusal in the still-image path"},
    {"id": "T11", "threat": "Attribution lost between candidate and handoff", "mitigation": "author + source_page carried in provenance and surfaced in the handoff record's human-facing output"},
    {"id": "T12", "threat": "OAuth/credential creep from the 'Google login allowed' statement", "mitigation": "no OAuth code, no token storage, no scope list in this phase"}
  ],
  "test_matrix": [
    {"id": "test_redact_url", "given": "pixabay URL containing key=SECRET_CANARY", "expect": "scrubbed form shows key=REDACTED; canary absent from the string"},
    {"id": "test_redact_exception", "given": "URLError raised with the raw URL", "expect": "error.v1 contains no canary"},
    {"id": "test_canary_never_persisted", "given": "full mock run with a canary key in env", "expect": "byte grep of var/ (all files incl. sqlite) and stdout finds zero occurrences"},
    {"id": "test_cdn_gets_no_auth", "given": "mock ingest", "expect": "fixture-cdn header log contains no authorization/cookie/x-api-key and no secret param"},
    {"id": "test_api_redirect_denied", "given": "302 from fixture-api", "expect": "exit 5, zero follow-up requests"},
    {"id": "test_api_key_header_only_pexels", "given": "pexels api call", "expect": "key in Authorization header only; absent from query string and from cache row"},
    {"id": "test_api_key_query_pixabay", "given": "pixabay api call", "expect": "key in query, cache row stores fingerprint only"},
    {"id": "test_cache_hit_no_request", "given": "two identical searches within TTL", "expect": "one fixture-api request total"},
    {"id": "test_cache_expiry", "given": "clock advanced past TTL", "expect": "re-fetch, new fetched_at, old row superseded"},
    {"id": "test_429_latch", "given": "429 with Retry-After", "expect": "exit 2, provider paused, next search emits no request until resume"},
    {"id": "test_foreign_host_rejected", "given": "api returns a URL on a non-allowlisted host", "expect": "download_hint.allowed=false, ingest exit 5"},
    {"id": "test_same_host_basename_hops", "given": "3 redirects same host same basename", "expect": "followed; 4th or a host change aborts"},
    {"id": "test_private_ip", "given": "hostname resolving to 10.x", "expect": "blocked before connect"},
    {"id": "test_jpeg_and_mp4_ok", "given": "fixture media", "expect": "verified, blob stored, provenance row, record_ref written"},
    {"id": "test_gif_animated", "given": "animated fixture gif", "expect": "animated=true, editing_compatible=false, still-path refusal"},
    {"id": "test_wrong_magic", "given": "mp4 extension with jpeg bytes", "expect": "rejected at magic stage"},
    {"id": "test_schema1_untouched", "given": "any run", "expect": "existing record table keys are exactly the schema 1 set (validated by a diff test)"},
    {"id": "test_extra_keys_rejected", "given": "provenance payload with an unknown key", "expect": "validator raises, nothing written"},
    {"id": "test_approval_replay", "given": "same token twice", "expect": "second ingest exit 3"},
    {"id": "test_approval_bound_fields", "given": "license snapshot changed after approval", "expect": "ingest exit 2, blocked_license"},
    {"id": "test_expired_candidate", "given": "candidate past TTL", "expect": "approve/ingest refused"},
    {"id": "test_dedupe", "given": "same blob via two providers", "expect": "one blob, two provenance rows"},
    {"id": "test_mixkit_noto_blocked", "given": "any ingest attempt", "expect": "exit 2, blocked_license"},
    {"id": "test_no_key_env", "given": "no env var", "expect": "exit 4 unverified, message says mock mode only"},
    {"id": "test_pagination_cap", "given": "--page 2", "expect": "rejected by CLI"}
  ],
  "unverified_requiring_real_key_or_docs": [
    "whether pexels API key issuance is currently open; the architecture must not encode an application path at all",
    "the current pexels video endpoint shape (older /videos/video/{id} vs a newer versioned path) — resolve by reading the current docs, then encode as a single per-provider constant",
    "pexels exact rate-limit header names and numeric windows",
    "pixabay's current numeric limits and whether the 24h cache clause is contractual or advisory",
    "whether the pexels 24h figure is a recommendation or a term",
    "actual CDN hostnames and TLS pins for both providers",
    "live robots.txt behavior and UA expectations on both CDNs",
    "real license text hashes for pexels/pixabay, and for mixkit/noto per item",
    "whether mixkit permits automated download for any item at all",
    "whether noto animation assets are covered by the repo LICENSE per item, and their real asset ids",
    "real search result field names and any silently-deprecated fields",
    "real 429/403 behavior and whether either provider geo-blocks"
  ],
  "milestones": [
    {"id": "M0", "scope": "contracts.py + redact.py + transport.py with both roles and allowlists; no provider code yet", "done_when": "redaction and transport-role tests pass with zero network"},
    {"id": "M1", "scope": "cache.py + ratelimit.py + pexels/pixabay search adapters + mock fixtures", "done_when": "search scenarios (hit, cache hit, expiry, 429, redirect) pass offline"},
    {"id": "M2", "scope": "store.py + candidate.py + ingest.py reusing the existing blob store and audit", "done_when": "select -> approve -> ingest -> dedupe -> provenance passes with fixture JPEG and MP4"},
    {"id": "M3", "scope": "license_gate.py + mixkit/noto blocked stubs + gif animated detection", "done_when": "blocked paths exit 2 and the gif is never treated as a still"},
    {"id": "M4", "scope": "never run without a key: a written checklist that runs the same scenarios live once a key exists", "done_when": "checklist reviewed by Codex; no code claims live verification before that"},
    {"id": "M5", "scope": "M4 follow-up: swap each verified constant (hosts, pins, endpoint paths, TTL basis) with a cited doc snapshot", "done_when": "license_registry entries flip to verified=true only with hashes"}
  ],
  "verification": {
    "commands": ["python -I -m pytest tests/prov -q", "python -m material_agent.prov.cli prov mock run --scenario mocks/scenarios/search_hit_jpeg.json", "python -m material_agent.prov.cli prov mock run --scenario mocks/scenarios/api_redirect_302.json", "grep -r CANARY var/ (must be empty)", "python -m material_agent.prov.cli prov doctor --provider pexels (key_present=false, license evidence=unknown)"],
    "note": "the -I flag matters because fixture media and mock payloads are untrusted input; keep test scripts out of the fixtures directory"
  },
  "explicitly_out_of_scope": [
    "scriptwriting, editing, and any Gamma-shared file",
    "OAuth, Google login, credential storage, scope changes",
    "creating or provisioning any API key",
    "live network verification, live downloads, live license fetching",
    "GIF editing or animated-image processing pipelines",
    "general-purpose gallery crawling, competitor datasets, pagination sweeps",
    "a framework, plugin system, or abstraction layer beyond the two transport roles"
  ],
  "decisions_for_codex_to_confirm": [
    "separate prov.sqlite3 vs new tables in the existing DB — plan recommends separate so schema 1 is provably untouched and the cache is trivially disposable",
    "candidate TTL default of 24h aligned to the cache TTL, vs a shorter approval window",
    "whether license snapshots stay manual-only this phase or get an optional credential-free doc fetch later",
    "whether the approval token is a file in var/prov/approvals/ or a row in approval — plan recommends the file so it is obviously gitignored and obviously single-use"
  ]
}
