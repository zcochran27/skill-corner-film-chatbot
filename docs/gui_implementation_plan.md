# GUI and backend implementation proposal

Prepared September 12, 2026. Proposal only; no application code or deployments changed.

## Recommendation

Build **React + TypeScript + Vite, a Canvas 2D tracking player, and a FastAPI backend that reuses the Python retrieval pipeline**. Start with validated question templates and editable filters so the core experience has no per-question AI charge. Add an optional natural-language model after execution accuracy improves.

User clarification: use a live backend that executes queries while the app is in use. Sleeping when unused and a wake-up delay are acceptable. A browser-only query engine is no longer a proposed delivery path; the comparisons below remain reference alternatives.

Use **a free Render FastAPI service as the first public-hosting candidate**, subject to memory and workload validation, with the React frontend hosted separately so it can display wake-up progress. Keep Oracle Always Free or an existing server as alternatives if the workload does not fit. Sleep is acceptable, so always-on availability is not a selection requirement. Render explicitly advises against production use of its free instances; this is a best-effort free deployment of an app designed and tested for production, not a provider-backed production availability guarantee. [Render limitations](https://render.com/docs/free).

Assumptions: the current 20-match collection, public tracking data, initially a small group of coaches, desktop first with tablet support, and no requirement for shared accounts at launch. Free means zero provider charges within allowances; hardware, electricity, development time, domain registration, and maintenance are separate.

## What exists and what must be finished

Repository evidence: [master plan](master_plan.md), [architecture](data_architecture.md), [parser executor](../scripts/parse_exec.py), and [project brief](../CLAUDE.md). These are recorded results, not fresh benchmarks from this planning session.

| Area | Current state | Required product work |
|---|---|---|
| Data | 20 matches, 94,517 events; about 1.8 GB Bronze including tracking; about 16 MB Gold on disk | Match manifest, availability metadata, reproducible serving exports |
| Retrieval | Event filters and advanced tracking predicates exist | A single runner for event, sequence, phase, negative, and tracking queries |
| Question coverage | Handwritten reference queries: 63 exact, 14 approximate, 3 unresolved | Preserve those distinctions in every API and result card |
| Natural-language parser | Latest documented sample: 7/13 row-comparable questions return the right rows | Fix and evaluate; this is not 63/80 natural-language accuracy |
| Clip output | Ranking and segmentation not built | Stable clip IDs, boundaries, deduplication, descriptions, evidence |
| GUI/player | No frontend in the inspected file inventory | Implement filters, question input, results and tracking playback |

The existing parser grader only executes event-level filters. Wrapping it in an API would not implement chain queries, negation, or tracking confirmation. The unified runner is a release dependency.

## GUI design

Preferred desktop layout: match and filter sidebar; question input across the main area's top; results list beside a large player. Keep the selected clip visible while scrolling results. On tablets, move filters into a drawer. On phones, stack query, selected player, and result list.

1. **Choose games.** Searchable match multi-select with team names, date, competition when available, and tracking availability. Add team, player, half, and minute filters. Derive choices from the selected matches; use IDs so equal player names cannot collide. Show selected-game count and offer an explicit all-games choice.
2. **Ask a question.** Text field, submit button, example questions and structured templates such as “Show [team]'s shots from [zone] in [games].” Show an editable “Interpreted as” row of conditions. Explain ambiguities before searching. Explicit game filters constrain the query; a contradictory team or game in the question prompts clarification rather than silently overriding scope.
3. **Review clips.** Each card shows match, player/team, match clock, duration, a short factual description, and why it matched. Label approximations next to the result. Show deduplicated clip count, separate unavailable-data count, and whether results are ranked or chronological. Paginate rather than rendering all event rows.
4. **Watch the animation.** Pitch, team colors, shirt numbers, ball and highlighted subject; play/pause, scrub, frame-step, 0.5x/1x/2x, previous/next clip, replay and fullscreen. Add optional player trails and an attacking-direction toggle after basic correctness.
5. **Keep useful work.** Initially save clip bookmarks and named playlists in browser storage, with JSON export/import. Explain that these stay on that device. Add account sync only when needed.

Descriptions should use data templates, for example “{player} carries into the final third; a shot follows {seconds} seconds later.” Include only measured facts; no model call per card. Missing metadata gets a neutral description, not invented commentary.

Required states: loading games, parsing, retrieving, confirming tracking, partial coverage, no matches, unsupported question, failed query with retry, missing animation frames and service waking up. Partial search results must identify themselves as partial. A data gap must never appear as evidence that an opponent failed to act.

### GUI implementation choices

Effort estimates below are planning judgments for the interface and basic player, assuming a usable API; they exclude parser repair and release hardening.

| Option | Software cost | Indicative effort | Pros | Cons / fit |
|---|---|---|---|---|
| **React + TypeScript + Vite** | $0 | 6–10 developer-days | Precise playback state, flexible results UI, reusable components | More frontend work; recommended long-term interface |
| Svelte or Vue + TypeScript | $0 | 6–10 days | Equally viable for a compact reactive interface | Choose if already familiar; changing frameworks does not reduce tracking or AI costs |
| Streamlit + custom JavaScript player | $0 | 2–4 days | Fastest route from existing Python to an internal trial | Custom component still needed for good playback; reruns/session model need care |
| FastAPI + server templates/HTMX + JavaScript player | $0 | 4–7 days | One Python application, smaller frontend toolchain | Player and playlist synchronization still require substantial JavaScript |
| Gradio + custom player | $0 | 2–4 days | Convenient question-to-result demo | More effort to create a polished multi-pane film-review workflow |
| Local web app packaged for desktop later | $0 software | Additional 3–7 days | Offline data, no public backend, can use local AI | Install/update packaging, local storage and hardware support |

React supports component-based interactive interfaces; Streamlit supports custom components. The relative effort and suitability above are project-specific judgments. [React documentation](https://react.dev/learn), [Streamlit components](https://docs.streamlit.io/develop/concepts/custom-components).

### Tracking player choices

| Renderer | Runtime cost | Advantages | Tradeoffs |
|---|---|---|---|
| **Canvas 2D** | User-device CPU/GPU; no render-server bill | Sufficient for players, ball and trails; straightforward playback loop | Need HTML controls and text alternatives for accessibility |
| SVG | User-device processing | Easy player selection, labels and inspectable elements | Larger trails/overlays need care; reasonable alternative for 23 objects |
| WebGL / PixiJS | User-device GPU | Useful for dense overlays or many simultaneous animations | Unnecessary complexity for a single pitch initially |
| Pre-rendered MP4/WebM | Offline/server rendering plus storage and delivery | Standard video controls, easy file sharing | Fixed labels/viewpoint; overlapping clips duplicate frames; export feature later |

Recommended implementation: render from timestamps at display refresh rate, interpolating adjacent 10 Hz samples for smooth visual motion. Interpolation does not increase measurement accuracy. Never interpolate across period boundaries, long gaps, substitutions, or missing identities. Use actual pitch dimensions and a tested coordinate transform; preserve the existing halftime/mirror-sign logic. Show missing or uncertain positions explicitly.

Clip boundaries start from matched event or sequence bounds and evidence frames. Trial default padding: 5 seconds before and 3 seconds after; clamp to period and available tracking. Keep a full-sequence option for longer buildups. Group using match + period + possession, but merge only nearby overlapping windows so distant actions in one possession are not collapsed. Store all contributing event IDs. Rank fuzzy concepts by their measured metric, with stable tie-breaking; do not invent a universal relevance score.

## Backend alternatives

These are execution architectures, independent of the hosting provider.

| Architecture | Provider execution cost | Performance expectation | Pros | Cons |
|---|---|---|---|---|
| **FastAPI + existing pandas/Parquet + indexed local tracking** | $0 on existing hardware or eligible free VM | Closest to recorded Python timings | Least rewrite; preserves advanced predicates | Needs a live Python host; measure resident memory, not compressed file size |
| FastAPI + DuckDB/Parquet + Python predicates | Same hosting choices | Benchmark against pandas; SQL may help joins and scans | Explicit SQL query plans; avoids loading unnecessary columns | Adds migration/parity work; does not accelerate model calls automatically |
| **Static React + DuckDB-Wasm or typed JavaScript query engine** | $0 query-server compute | Target 0.05–0.5 s simple queries after load; device-dependent | No sleeping backend; strong route to a $0 public app | Port query semantics or publish a supported subset; browser memory, downloads and CORS matter |
| Static frontend + Cloudflare Worker + precomputed catalog | Free within quotas | Good for small indexed lookups | Lightweight APIs with no VM maintenance | Free Worker CPU is unsuitable for current multi-second Python geometry |
| Python monolith using Streamlit or Gradio | Free locally or eligible host | Same underlying retrieval, plus UI/session overhead | Fast internal validation | Per-session memory and custom playback integration |
| Postgres/Supabase + separate Python worker | Free within quotas | Indexed metadata queries; geometry remains external | Useful later for users, shared playlists and annotations | Extra moving parts; database does not replace tracking worker |

[FastAPI](https://fastapi.tiangolo.com/) provides typed API development. [DuckDB-Wasm](https://duckdb.org/docs/lts/clients/wasm/overview) runs in the browser; its [browser filesystem constraints](https://www.duckdb.org/docs/lts/clients/wasm/extensions) require appropriate CORS support. Free [Cloudflare Workers](https://developers.cloudflare.com/workers/platform/pricing/) allow 100,000 requests/day and 10 ms CPU per invocation; waiting for an external response is distinct from executing geometry.

For a browser edition, support event filtering first. Precompute reusable metrics offline, preserving parameter definitions and coverage. Advanced parameterized queries must either be ported and verified against Python or explicitly unavailable. Precomputing answers to 80 questions does not create an unrestricted question engine.

### Proposed API and data contracts

| Endpoint | Purpose |
|---|---|
| `GET /api/matches` | Available matches and data version |
| `GET /api/facets?match_id=...` | Valid teams, players, event choices and ranges |
| `POST /api/queries` | Validate scope and question/template; return result or asynchronous job ID |
| `GET /api/queries/{id}` | Interpretation, status, coverage, errors and paginated clip results |
| `GET /api/clips/{id}/tracking` | Bounded tracking window or chunk references |
| `GET /api/health` | Readiness and loaded data version |

Query requests carry selected match IDs, structured filters, text or template ID, and data version. Responses carry query mode, executed interpretation, answerability disclosure and coverage. Each clip carries stable ID, match/period, start/end times and frames, contributing events, subjects, description, ranking metric when applicable, evidence and tracking reference.

Use a bounded executor for CPU-heavy tracking work; an async HTTP handler alone does not parallelize pandas or geometry. Start with one resident dataset and limited job concurrency to avoid duplicating memory. Long work gets a job ID and polling; cancellation and timeouts prevent stale searches consuming resources. Durable jobs need a persistent store; on ephemeral demo hosting, disclose interrupted jobs and allow retry.

Preserve Bronze → Silver → Gold. The documented indexed-tracking exception remains through the existing accessor. Publish a separate versioned serving export: Gold-derived search tables, metadata promoted through the existing layers, and chunked normalized coordinates through the tracking accessor. Do not make the GUI read raw Bronze CSVs or overwrite the analytical source data.

Serve about 30–60 seconds per tracking chunk, with a manifest of timestamps and stable player IDs. Fetch the selected clip and prefetch only the next likely chunk. Start with compact compressed JSON; consider binary arrays after measuring. Keep network data fetching out of the animation loop.

## Free and near-free hosting choices

Provider documentation checked September 12, 2026. Free allowances can change; verify the account's actual limits before deployment. Figures exclude model usage and development labor.

| Hosting choice | $0 possibility | Important limits / drawbacks | Best use |
|---|---|---|---|
| **Local machine / existing home server** | No hosting subscription | Electricity, uptime, backups, network administration; public access requires deliberate setup | First complete implementation and private coaching use |
| **Cloudflare Pages static edition** | Yes, within limits | 500 builds/month, 20,000 files/site, 25 MiB/file; requires browser execution/export work | Public free app with supported deterministic queries |
| Pages + R2 + optional Worker | Yes, within allowances | R2 is metered with possible overages; free Worker cannot run heavy geometry | Static frontend and efficient tracking delivery |
| **Oracle Always Free VM** | Yes, if capacity/account eligibility allows | Current docs: 1,500 OCPU-hours and 9,000 GB-hours/month, equivalent to 2 OCPUs/12 GB; idle instances may be reclaimed | Closest free cloud fit for existing Python, with operator maintenance |
| **Render free Python service** | Yes within allowances | Sleeps after 15 idle minutes; wake-up about a minute plus app initialization; 750 shared instance-hours/month; ephemeral filesystem, no free persistent disk | Preferred first live-backend candidate given accepted sleep; validate memory and accept provider's free-service limitations |
| Streamlit Community Cloud | Free | Resource limits and sleep after 12 hours without traffic; profile memory and data setup | Fast internal/demo GUI |
| Hugging Face Spaces | Static hosting free; do not assume free new Python hosting | Current overview says Gradio/Docker compute requires a paid plan even though CPU Basic has no hourly charge; compute can sleep | Static demo or already-eligible account |
| Supabase Free | Free within quotas | 500 MB database, 1 GB file storage; pause/quota constraints; raw tracking exceeds storage allowance | Optional accounts and shared playlist metadata |
| Vercel Hobby | Free for qualifying use | Personal/non-commercial restriction; not a default choice for a commercial coaching service | Personal frontend prototype |

Sources: [Pages limits](https://developers.cloudflare.com/pages/platform/limits/), [R2 pricing](https://developers.cloudflare.com/r2/pricing/), [Oracle Always Free](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm), [Render free](https://render.com/docs/free), [Streamlit hosting](https://docs.streamlit.io/deploy/streamlit-community-cloud) and [sleep behavior](https://docs.streamlit.io/deploy/streamlit-community-cloud/manage-your-app), [Spaces overview](https://huggingface.co/docs/hub/spaces-overview), [Supabase pricing](https://supabase.com/pricing), [Vercel Hobby](https://vercel.com/docs/plans/hobby).

R2 Standard includes 10 GB-month storage, 1 million Class A operations and 10 million Class B operations monthly, with no egress charge. That can fit this corpus, but is not a hard spending cap. Its public `r2.dev` endpoint is for development; use an appropriate production route, such as a controlled Worker, or a custom domain whose registration may cost money. [R2 pricing](https://developers.cloudflare.com/r2/pricing/), [public bucket guidance](https://developers.cloudflare.com/r2/buckets/public-buckets/).

Use Pages for the frontend and optionally compact playback assets; run query interpretation and retrieval on the live Python service. Browser-only querying is outside the revised scope. Preserve a containerized migration path if the free host's memory or usage limits are exceeded.

### Sleep, restart and freshness behavior

- Opening the app makes a readiness request to wake the backend. Show “Starting the search service” and retain the user's filters and question. Retry readiness with bounded backoff; provide a manual retry if startup fails. Submit the query once ready, avoiding duplicate jobs.
- Do not keep the backend awake with background pings. Poll only during startup or an active job; stop when complete or the page is inactive. Sleeping between visits is expected.
- Build and validate the data release offline. Bundle the small query tables and indexes into a deployment artifact; keep tracking in a durable versioned source or image layer. Load only necessary columns/matches into memory and fetch bounded tracking windows. Do not download and rebuild the entire corpus on every wake-up.
- The existing tracking accessor expects local indexed JSONL. Either package those immutable files with the deployment, or implement and validate a range/chunk-backed accessor with equivalent coverage semantics. Remote object storage is not a drop-in replacement for local file seeks. Benchmark both startup and advanced queries before choosing.
- Treat runtime disk and in-memory jobs/caches as disposable. Store saved user work outside the sleeping service or in browser storage initially. After a restart, interrupted jobs return an expired/retry state rather than disappearing into endless polling.
- Every response and cache entry identifies the data release. Check the published release manifest on startup and periodically during active use; on a version change, switch atomically to a validated release and invalidate affected caches. Expose the active release and last update in the interface. Failed refreshes explicitly report that the last validated release remains active.
- Sleeping and data freshness are separate. New SkillCorner repository data needs an ingestion/build/publish step; waking the API alone does not refresh upstream data. The app serves the latest successfully published release and discloses its timestamp.
- Measure full wake-to-ready time, warm query latency, resident memory and restart recovery. Provider wake-up is about one minute; application initialization can add time. Include bandwidth and external-storage traffic in the quota test. [Render lifecycle and limits](https://render.com/docs/free).

## Free question interpretation options

| Approach | Per-question AI bill | Planning latency, excluding retrieval | Pros | Cons |
|---|---|---|---|---|
| **Question templates + explicit filters** | $0 | Target under 50 ms | Testable, deterministic, available offline | Supports a defined question vocabulary |
| Rules/synonyms/entity lookup → validated query | $0 | Target under 100 ms | Natural phrasing for common questions without a model | Paraphrases and compound/negative wording need extensive tests |
| Local model through Ollama | $0 API fees | Trial range 2–30 s on suitable GPU; CPU can be much slower | Private, no API quota | Hardware/electricity/model memory; correctness unproven |
| Browser model through WebLLM | $0 server inference | Trial range 3–30+ s after model load | Client-side inference | Potentially large initial model download, WebGPU/device compatibility, mobile pressure |
| Eligible Gemini API free tier | $0 within model/account limits | Trial range 1–10+ s | No local model hardware | Rate limits, availability and free-tier data-use terms; fallback required |
| User-supplied provider key | User pays | Provider-dependent | App owner need not subsidize queries | Not actually free; key handling and onboarding complexity |
| Existing eight-gate parser | Recorded $0.05–$0.07/question | Recorded 30–50 s | Already integrated | Too costly/slow for default free use and insufficient current accuracy |

Latency ranges for alternative models are hypotheses for benchmarking, not measured comparisons or promises. [Ollama local operation](https://docs.ollama.com/faq), [WebLLM](https://github.com/mlc-ai/web-llm), [Gemini pricing and free-tier terms](https://ai.google.dev/gemini-api/docs/pricing).

Recommended routing: exact template → tested rule parser → optional model → clarification when unsupported or ambiguous. Every path emits the same typed query and passes the same validators. The existing `--offline` keyword stub is a plumbing test, not the production free parser.

Retain the current dependency-ordered gate design initially. Skipping demonstrably irrelevant gates, reducing reasoning effort or testing a compact parser are experiments gated by execution quality. Do not run dependent gates in parallel just to reduce latency. Cache interpretation by normalized question, match/entity scope, parser/schema version and data version; cache retrieval by the complete validated query and data version.

## Cost and processing-time comparison

Recorded repository measurements: simple event retrieval median about 17 ms; advanced tracking questions about 2–15 s; parser about 30–50 s. Indexed frame reads of about 1.8 ms warm/6 ms cold per event window are a subcomponent, not end-to-end query time. Silver/Gold builds are approximately 60 s/25 s and belong offline. These machine-specific observations must be repeated on the deployment target.

| User action | Planning response time | Main cost |
|---|---|---|
| Template + simple filter, warm API | Target 0.1–0.5 s end-to-end | Small server/client compute + network |
| Template + advanced tracking | Existing retrieval 2–15 s, plus network/queue | CPU; candidate count matters |
| Current AI + simple retrieval | About 30–50 s plus overhead | $0.05–$0.07 recorded parser cost |
| Current AI + advanced tracking | Roughly 32–65 s plus overhead | Parser + tracking CPU |
| Cached result | Target 0.05–0.3 s after warm load | Cache lookup + network |
| First tracking playback | Target under 1 s on adequate connection | Chunk transfer/decompression |
| Render free after idle | Add provider wake-up of about a minute | No charge within free limits; user delay |

**Illustrative monthly workload:** 1,000 questions, 20% needing tracking, 5,000 played clips/chunks, average transferred payload 200 KB. These assumptions are for sizing, not observations.

- Question templates: $0 inference. Existing parser for all questions: approximately $50–$70. At 10,000 questions: $500–$700. If templates/cache resolve 90% and only 100 questions use that parser: $5–$7 at 1,000 questions.
- Retrieval service-time estimate: `800 × 0.017 s + 200 × (2–15 s)` = about 7–50 minutes/month. This is neither a CPU billing measurement nor a concurrency guarantee; free servers can still be memory- or burst-limited.
- Tracking transfer: `5,000 × 200 KB` ≈ 1 GB/month, before metadata, prefetch and retries. At 100,000 such plays, about 20 GB. Byte volume and object request counts are separate limits.
- A 20-second clip at 10 Hz with 23 objects, two float32 coordinates each, has a 36.8 KB coordinate-only lower bound. IDs, timestamps, masks, ball height and JSON overhead add bytes; measure compressed transfer size.
- A 20-second rendered video at an assumed 1 Mbps is about 2.5 MB, or about 12.5 GB for 5,000 plays. This illustrates why coordinate playback should precede video exports; actual codecs and scene complexity change the comparison.

Expected monthly provider charges for a templates-only app: $0 locally; potentially $0 for the static edition within limits; potentially $0 for the eligible VM route. Adding metered AI makes the same app nonzero even when hosting is free. Cloud spend is hosting + storage + operations/bandwidth + uncached model calls; local operation adds power and maintenance instead.

## Implementation sequence and release gates

Estimates assume one developer familiar with this repo. Total for the recommended scoped production release: approximately 4–7 weeks, with parser quality the largest uncertainty. Browser parity or unrestricted natural language can extend it.

| Step | Indicative effort | Concrete deliverable and acceptance |
|---|---|---|
| 1. Query contract and supported scope | 2–3 days | Shared typed query/result/clip schemas; selected match IDs constrain every retrieval branch; choose 10–15 validated free templates |
| 2. Unified runner and clip output | 4–7 days | Routes event/sequence/negative/phase/tracking queries, implements dedup/ranking/descriptions; compares resulting event sets to references |
| 3. API and serving data | 2–4 days | Match/facet endpoints, bounded jobs, tracking-window endpoint, versioned exports, caching and failure responses |
| 4. GUI and animation | 6–10 days | Complete choose-games → ask → inspect → play flow; keyboard controls, tablet layout, local bookmarks |
| 5. Optional model evaluation | 3–5 days initially | Compare rules/local/free API/current parser on execution correctness, cost, p50/p95 latency; ship only supported modes |
| 6. Deployment and hardening | 3–5 days | Target-host memory/load tests, reproducible build, rollback, errors/usage logging, restart recovery and documented limits |

First useful vertical slice: one real match, a deterministic shot question, deduplicated descriptions and working tracking playback. Then expand to all selected games and advanced questions. Do not wait for unrestricted language understanding to validate the film-review experience.

Production release gates:

- All 80 reference questions exercised; the three unsupported cases explain missing data and all 14 approximations disclose the actual proxy. Free templates require exact parity with their intended reference results. Independently evaluate paraphrases; never equate correct field names with correct answers.
- Negation tested with missing tracking, no-results tested independently from invalid filters, match scoping enforced across all branches, and ranked questions preserve metric ordering.
- Clip playback checked against actual frames around halftime, substitutions, gaps, ball height, evidence timestamps and repeated seeks. Tests ensure one clip's tracking never appears under another card after a rapid selection change.
- Define a small-load envelope initially, such as 10 simultaneous users with at most two heavy jobs executing. Measure p50/p95 latency, queue time, memory and first-frame load on that actual host. Display backpressure rather than attempting unlimited concurrency.
- Validate typed fields and bound match counts, clip durations, page sizes and job time. Never execute model-generated Python or unrestricted SQL. Keep provider credentials server-side; public static bundles contain none.
- Use HTTPS, appropriate CORS, safe description rendering, pinned runtime dependencies and a reproducible container. Avoid logging keys or unnecessary full questions. Cache keys include all scope and version fields.
- Serve immutable data releases with a manifest/checksum and rebuild instructions. Persist shared annotations only on durable storage. Document unavailable-host behavior, retry/cancel and rollback.
- Keep upstream attribution and license notices in published artifacts; the upstream repository includes an MIT license. Confirm the exact distribution's notices when packaging data. [SkillCorner license](https://github.com/SkillCorner/opendata/blob/master/LICENSE).

## Decision to implement first

Use the React/Canvas interface with a live FastAPI backend and existing pandas initially. Deliver the complete question-to-clip flow, then test deployment on Render Free with explicit wake-up, retry and versioned-data handling. Sleep while unused is an accepted behavior. Move to an eligible free VM or existing server if memory or quota tests fail; do not replace backend execution with precomputed question answers. Defer accounts, embeddings, multi-angle/3D playback and rendered video export until the core question-to-clip loop is trusted.
