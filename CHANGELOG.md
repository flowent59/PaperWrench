# Changelog

## [0.5.0](https://github.com/flowent59/PaperWrench/compare/v0.4.0...v0.5.0) (2026-10-01)


### Features

* **automation:** saved bulk-processing rules with dry-run-first execution ([#74](https://github.com/flowent59/PaperWrench/issues/74)) ([b07375c](https://github.com/flowent59/PaperWrench/commit/b07375c2893653063733b11510d56d54514482c0))
* **automation:** schedule and monitor approved rules ([562b64f](https://github.com/flowent59/PaperWrench/commit/562b64f5ce3a91af6395a62d7d34d9751ace8e3a))
* **automation:** schedule and monitor approved rules ([f81ccdb](https://github.com/flowent59/PaperWrench/commit/f81ccdbc679a2c98a9b2014f741c4bc66273d9cb))
* **rollback:** restore selected documents safely ([eb45762](https://github.com/flowent59/PaperWrench/commit/eb45762e384ced9e26ee4360d348aefe9d8615a1))
* **rollback:** restore selected documents safely ([be0fffe](https://github.com/flowent59/PaperWrench/commit/be0fffeb1b5f7caff0c980b1235a81ef3b9f552a))


### Bug Fixes

* **automation:** recheck approved revision at job creation ([d013da5](https://github.com/flowent59/PaperWrench/commit/d013da5d073a20993301eab8775648b6e3d821f8))

## [0.4.0](https://github.com/flowent59/PaperWrench/compare/v0.3.0...v0.4.0) (2026-09-30)


### Features

* **analytics:** add filtered custom-field reports ([#71](https://github.com/flowent59/PaperWrench/issues/71)) ([eac7ec6](https://github.com/flowent59/PaperWrench/commit/eac7ec6ac49c033328c74589438369cfd5ead93c))
* **dashboard:** add permission-aware document analytics ([#70](https://github.com/flowent59/PaperWrench/issues/70)) ([80633b6](https://github.com/flowent59/PaperWrench/commit/80633b610928f123a65f5bdcc7d0987208fa2a25))

## [0.3.0](https://github.com/flowent59/PaperWrench/compare/v0.2.0...v0.3.0) (2026-09-30)


### Features

* **collections:** support dynamic saved filters ([#65](https://github.com/flowent59/PaperWrench/issues/65)) ([18a918c](https://github.com/flowent59/PaperWrench/commit/18a918c649098443557b4c1e408f81971841d5d1))
* **ux:** guide transformation workflow ([#68](https://github.com/flowent59/PaperWrench/issues/68)) ([88bd263](https://github.com/flowent59/PaperWrench/commit/88bd263f66ade406260a2383a46974626127d09b))
* **views:** save and reuse Explorer views ([#67](https://github.com/flowent59/PaperWrench/issues/67)) ([4d61341](https://github.com/flowent59/PaperWrench/commit/4d6134122ea657963d0616e325ac46347c3910b7))

## [0.2.0](https://github.com/flowent59/PaperWrench/compare/v0.1.1...v0.2.0) (2026-09-29)


### Features

* **auth:** add per-user Paperless sessions ([0f3bdb0](https://github.com/flowent59/PaperWrench/commit/0f3bdb0fa7d4627ff02e66c0664d3cc82f4a567b))
* **auth:** authenticate users with their own Paperless token ([3757da5](https://github.com/flowent59/PaperWrench/commit/3757da5d5b0481c29370b681b936ffffcdc5b366))
* **auth:** enforce per-user resource isolation ([8ea1477](https://github.com/flowent59/PaperWrench/commit/8ea147764ac20c29c3e124d58e4c6b535c77cec6))
* **auth:** enforce per-user resource isolation ([9a5b162](https://github.com/flowent59/PaperWrench/commit/9a5b16221e3071a7a68245b598cb4576e358ff1a))
* **i18n:** add French and English interface ([1b389d0](https://github.com/flowent59/PaperWrench/commit/1b389d0a168b9eb6ec108cda1b36e6afa9564022))
* **i18n:** add French and English UI ([320e372](https://github.com/flowent59/PaperWrench/commit/320e372532856839c663678582ff8fe19c029aa4))


### Bug Fixes

* **auth:** normalize Paperless profile identity ([25ba43b](https://github.com/flowent59/PaperWrench/commit/25ba43b5f3432465e30460f2e204bc3f1d427eb1))

## [0.1.1](https://github.com/flowent59/PaperWrench/compare/v0.1.0...v0.1.1) (2026-09-26)


### Bug Fixes

* **a11y:** prevent low contrast during button selection ([11b2538](https://github.com/flowent59/PaperWrench/commit/11b25381582933a6d07c26573d346d4fffbebaf8))
* **ci:** find draft releases before publishing images ([0bd02d6](https://github.com/flowent59/PaperWrench/commit/0bd02d63a6927b7363b9a68bcf33cf251ede6de3))
* **ci:** recover draft releases before Docker publication ([8ad10f8](https://github.com/flowent59/PaperWrench/commit/8ad10f859b44866381ea9cde518781a5c6ae910c))

## 0.1.0 (2026-09-26)


### Features

* add bounded M7 dry-run previews and review confirmation ([317072a](https://github.com/flowent59/PaperWrench/commit/317072acfdbc034e1c809b4afc815608881b0d5c))
* add document schemas and read-only conformance ([d4e6b5f](https://github.com/flowent59/PaperWrench/commit/d4e6b5f2dd8ec8131eaebfb141b7a27ded72f80f))
* add durable M8 job engine and history ([70d674c](https://github.com/flowent59/PaperWrench/commit/70d674c2a76cd66bf06ea32a343aa093beb6fbb2))
* add Inspector and typed inline editing ([#8](https://github.com/flowent59/PaperWrench/issues/8)) ([cb5803c](https://github.com/flowent59/PaperWrench/commit/cb5803cae6491b005a639dbfcb51ca489daed4f2))
* add pure M6 transformation engine ([0d18072](https://github.com/flowent59/PaperWrench/commit/0d18072a43af53c717a7cdf2d076b67ba2d456d4))
* add read-only M11 quality view and exact Explorer drill-down ([4b38229](https://github.com/flowent59/PaperWrench/commit/4b382295d840ace5936800d1e2abbeeae8c619fb))
* add safe linked rollback jobs with guarded preview ([aeb30df](https://github.com/flowent59/PaperWrench/commit/aeb30dfc854ca833e56bf3098b0fa1a8aeef30b9))
* add static collections and Explorer membership flow ([790ff1c](https://github.com/flowent59/PaperWrench/commit/790ff1c33ff1c27d8802b4d7b6e9b6d04c234a96))
* **m0:** project foundations, backend/frontend skeleton, CI and ADRs ([b074638](https://github.com/flowent59/PaperWrench/commit/b0746387470c94cd99e0008046c6060d1aa2d539))
* **m0:** project foundations, backend/frontend skeleton, CI and ADRs ([e2576fe](https://github.com/flowent59/PaperWrench/commit/e2576fecbb62fb939ff0cdebb64a87ef74a7a411))
* **m2:** normalized models, Metadata Registry, 401/403 split, metadata API ([6483955](https://github.com/flowent59/PaperWrench/commit/6483955ff2806e8c3d0f2847551a23575272d29f))
* **m3:** Explorer page - TanStack Table DataGrid, dynamic custom-field columns, cross-page selection ([4e8e08e](https://github.com/flowent59/PaperWrench/commit/4e8e08e0b795d1800368b3fedaf16d8c592fe5c4))
* **m3:** normalized documents API with ordering allowlist and page-size validation ([1ced846](https://github.com/flowent59/PaperWrench/commit/1ced84610f4a8b64bc2cbf3e3e608afabdf8bb64))
* **m4:** Filter Builder UI and Explorer on the Filter Engine ([9267f84](https://github.com/flowent59/PaperWrench/commit/9267f842f739bb120b8d71d7cef2ffcb6e76e32a))
* **m4:** Filter Engine - FilterSet domain model, strict compiler, no fallback ([8181bc6](https://github.com/flowent59/PaperWrench/commit/8181bc6f2775b3b7858c38797ed74c72ca6ee793))
* M7 Dry Run with bounded previews and review confirmation ([24acce3](https://github.com/flowent59/PaperWrench/commit/24acce3df258cd04fd77749b9d6f9c10154a295e))
* **paperless:** PaperlessClient as the sole API boundary, verified live ([fe9359b](https://github.com/flowent59/PaperWrench/commit/fe9359b09b23da0cd8f6d62c7d383996c4ba7d25))


### Bug Fixes

* **ci:** wait for the Golden Dataset to be fully consumed before live tests ([8c424d5](https://github.com/flowent59/PaperWrench/commit/8c424d53cf5bfdb13408ee8dc0942d1e7a90e13c))
* complete Vitest 5 and React Router 7 migration ([#28](https://github.com/flowent59/PaperWrench/issues/28)) ([11ca24f](https://github.com/flowent59/PaperWrench/commit/11ca24f87d4ce0d9fb0e38e56cda74bc5838f03e))
* enforce coordinated Paperless client write boundary ([#7](https://github.com/flowent59/PaperWrench/issues/7)) ([212e0f3](https://github.com/flowent59/PaperWrench/commit/212e0f397fa86d8eb418dfd5c215b039544f9b2b))
* migrate Vitest and React Router for Node 22 ([23c629c](https://github.com/flowent59/PaperWrench/commit/23c629c996bda3927aefa3eff7c2d69a2469cc0d))
* **paperless:** 409 Conflict is non-retryable by default; align roadmap numbering ([3a04e80](https://github.com/flowent59/PaperWrench/commit/3a04e800381ba959d0d195a4a896a08c013369e8))
* redact secrets in validation error paths ([1627e3b](https://github.com/flowent59/PaperWrench/commit/1627e3b0de96d605176d77978a85079c8b5c5c28))
* **tests:** match accented custom-field names seeded by the Golden Dataset ([f2a9a32](https://github.com/flowent59/PaperWrench/commit/f2a9a3247ea6d08c31b548e4824a2c139585a6f1))
* **tests:** the live API tests need a file-backed SQLite, not an in-memory one ([a4c961b](https://github.com/flowent59/PaperWrench/commit/a4c961bcfb7369e74a0fd00a3a712f2959798354))
* type legacy migration scalar results explicitly ([fcf1635](https://github.com/flowent59/PaperWrench/commit/fcf163537bf153006ab967be8991df681af22700))

## Changelog

Release Please updates this file in release pull requests. The first release
will summarize the Conventional Commits since the initial repository commit.
