# Changelog

## [1.0.3](https://github.com/xnoto/opencode-agent-hub/compare/v1.0.2...v1.0.3) (2026-02-13)


### Bug Fixes

* **daemon:** fix coordinator startup, session matching, and self-registration race ([6f14459](https://github.com/xnoto/opencode-agent-hub/commit/6f1445907843e162223a7e426aa892c55488b3c2))
* **daemon:** fix coordinator startup, session matching, and self-registration race ([1d5e658](https://github.com/xnoto/opencode-agent-hub/commit/1d5e658a4fed11c91b47291e992023ed11dd8481))
* **daemon:** keep opencode/ as model provider prefix instead of anthropic/ ([c9cffa9](https://github.com/xnoto/opencode-agent-hub/commit/c9cffa903a59a56858f654263d506b0a261f000f))

## [1.0.2](https://github.com/xnoto/opencode-agent-hub/compare/v1.0.1...v1.0.2) (2026-02-13)


### Documentation

* **readme:** fix homebrew tap name in install commands ([5911304](https://github.com/xnoto/opencode-agent-hub/commit/591130465f8ff3bc883d5b7c4b9746afb4f3b804))

## [1.0.1](https://github.com/xnoto/opencode-agent-hub/compare/v1.0.0...v1.0.1) (2026-01-26)


### Documentation

* **readme:** add demo video ([88fd196](https://github.com/xnoto/opencode-agent-hub/commit/88fd196269cd519407e3e50d5d635ed186376265))
* **readme:** add demo video ([6b1eca0](https://github.com/xnoto/opencode-agent-hub/commit/6b1eca01ff66945840c18e14bf8080b29263c93a))

## [1.0.0](https://github.com/xnoto/opencode-agent-hub/compare/v0.5.5...v1.0.0) (2026-01-25)


### ⚠ BREAKING CHANGES

* **daemon:** None - backward compatible with existing setups

### Features

* **daemon:** add session-based agent identity and config file support ([85c13e5](https://github.com/xnoto/opencode-agent-hub/commit/85c13e5dd1d0a8ca103c745da5fe11d0cb3d02da))
* **daemon:** add session-based agent identity and config file support ([e6ccc3b](https://github.com/xnoto/opencode-agent-hub/commit/e6ccc3b26da872cd1401cea0018df0b1d5481ed5))

## [0.5.5](https://github.com/xnoto/opencode-agent-hub/compare/v0.5.4...v0.5.5) (2026-01-23)


### Bug Fixes

* sign RPM packages with GPG key ([4811d60](https://github.com/xnoto/opencode-agent-hub/commit/4811d605e78b32b560360139a6fc56f403b964e3))
* sign RPM packages with GPG key ([e15356c](https://github.com/xnoto/opencode-agent-hub/commit/e15356c0ea7af64844fe77b5fe985b1ceab73f05))

## [0.5.4](https://github.com/xnoto/opencode-agent-hub/compare/v0.5.3...v0.5.4) (2026-01-23)


### Bug Fixes

* add --pinentry-mode loopback for non-interactive GPG signing ([c48e1e7](https://github.com/xnoto/opencode-agent-hub/commit/c48e1e757e5ea850f2a98fff893721cef580e1a9))
* add --pinentry-mode loopback for non-interactive GPG signing ([e19964f](https://github.com/xnoto/opencode-agent-hub/commit/e19964ff15d9ab654d067ac226ab60f73b1fc101))

## [0.5.3](https://github.com/xnoto/opencode-agent-hub/compare/v0.5.2...v0.5.3) (2026-01-23)


### Bug Fixes

* use colon format for reliable GPG key fingerprint parsing ([d1e4bc0](https://github.com/xnoto/opencode-agent-hub/commit/d1e4bc030d2095a8e495ef1641546bb0c8b283e3))
* use colon format for reliable GPG key fingerprint parsing ([b6da62c](https://github.com/xnoto/opencode-agent-hub/commit/b6da62c84c4a030f321de87a1c421737a29211ee))

## [0.5.2](https://github.com/xnoto/opencode-agent-hub/compare/v0.5.1...v0.5.2) (2026-01-23)


### Bug Fixes

* use inputs.tag_name check for workflow_call condition ([b5f31c2](https://github.com/xnoto/opencode-agent-hub/commit/b5f31c2dba1b660c361e4353d55bca392c31e57f))
* use inputs.tag_name check instead of event_name for workflow_call ([6c04bb4](https://github.com/xnoto/opencode-agent-hub/commit/6c04bb4a411cae88e99f0e4b4f6af64f96e59f78))

## [0.5.1](https://github.com/xnoto/opencode-agent-hub/compare/v0.5.0...v0.5.1) (2026-01-23)


### Bug Fixes

* chain release-packages workflow from release-please ([25a136f](https://github.com/xnoto/opencode-agent-hub/commit/25a136f5355c911838031847d1ade1a973491433))
* chain release-packages workflow from release-please ([5a600d3](https://github.com/xnoto/opencode-agent-hub/commit/5a600d3ebc527fdd6ac7dfcb7f6454cb3215c59e))

## [0.5.0](https://github.com/xnoto/opencode-agent-hub/compare/v0.4.0...v0.5.0) (2026-01-23)


### Features

* add GitHub Pages package repository ([4c55392](https://github.com/xnoto/opencode-agent-hub/commit/4c5539291a8253bef91b4bed0e28089ba506a3a3))
* add Linux packaging support and --install-service flag ([fdce08d](https://github.com/xnoto/opencode-agent-hub/commit/fdce08d9c3fce3877a245d98193a42ba81e4e899))
* add Linux packaging support and --install-service flag ([a9e713d](https://github.com/xnoto/opencode-agent-hub/commit/a9e713df1b66d8062ad432089cd102df3d9337dc))
* add platform check for service installation flags ([39938b2](https://github.com/xnoto/opencode-agent-hub/commit/39938b2c500985514b924c6136e2658acb72cb64))


### Bug Fixes

* improve preflight error messages and relax package deps ([7832285](https://github.com/xnoto/opencode-agent-hub/commit/78322853ead9f23b61eba2231d40bfd69857ad97))
* resolve RPM and DEB build failures ([9b2e946](https://github.com/xnoto/opencode-agent-hub/commit/9b2e9467d9bcfdf860b6d52f72eb00c18f7a005e))

## [0.4.0](https://github.com/xnoto/opencode-agent-hub/compare/v0.3.5...v0.4.0) (2026-01-23)


### Features

* **daemon:** add preflight check for agent-hub MCP configuration ([db16054](https://github.com/xnoto/opencode-agent-hub/commit/db1605487bc18dc2a1c4c682d5e8fb175d8641d5))
* **daemon:** add preflight check for agent-hub MCP configuration ([534a4e1](https://github.com/xnoto/opencode-agent-hub/commit/534a4e1a4b7d80e72d7ec3de96c86dbf10cc9634))

## [0.3.5](https://github.com/xnoto/opencode-agent-hub/compare/v0.3.4...v0.3.5) (2026-01-23)


### Bug Fixes

* correct PyPI PAT inputs ([5705b7d](https://github.com/xnoto/opencode-agent-hub/commit/5705b7d0b0a8257e9deefc4b73411224c92e3bf5))
* correct PyPI PAT inputs ([828146e](https://github.com/xnoto/opencode-agent-hub/commit/828146e1c88bed14f9ffd44ff888805ca2c2963e))
* move Homebrew update into publish ([0764412](https://github.com/xnoto/opencode-agent-hub/commit/07644126b5cca6d21445b9749a069429166ea831))

## [0.3.4](https://github.com/xnoto/opencode-agent-hub/compare/v0.3.3...v0.3.4) (2026-01-23)


### Bug Fixes

* publish to PyPI with PAT ([142a981](https://github.com/xnoto/opencode-agent-hub/commit/142a981bc0a0fb15431b9bbeef83961f0e5c1a98))
* publish to PyPI with PAT ([fa9d9f8](https://github.com/xnoto/opencode-agent-hub/commit/fa9d9f8bfd63e9b204638dc15869aff44bcb514b))

## [0.3.3](https://github.com/xnoto/opencode-agent-hub/compare/v0.3.2...v0.3.3) (2026-01-23)


### Bug Fixes

* restore PyPI publish and shutdown handling ([ee0ea59](https://github.com/xnoto/opencode-agent-hub/commit/ee0ea59ac86bdd9b8ea0ca27f3cb636b86471a16))
* restore PyPI publish and shutdown handling ([2dcb0d3](https://github.com/xnoto/opencode-agent-hub/commit/2dcb0d39b2a250a694f072bb075b49ebc9e01fb6))

## [0.3.2](https://github.com/xnoto/opencode-agent-hub/compare/v0.3.1...v0.3.2) (2026-01-22)


### Bug Fixes

* trigger Homebrew update on release ([30b5090](https://github.com/xnoto/opencode-agent-hub/commit/30b5090b30a555d98b41436856f3233ea4907ca9))
* trigger Homebrew update on release ([2a22ef0](https://github.com/xnoto/opencode-agent-hub/commit/2a22ef03769ad4ab7bcf2f882bdf27d27e4055bf))

## [0.3.1](https://github.com/xnoto/opencode-agent-hub/compare/v0.3.0...v0.3.1) (2026-01-22)


### Bug Fixes

* ensure tag exists for GitHub release ([0765017](https://github.com/xnoto/opencode-agent-hub/commit/0765017038563627776bcf8e0a838ec009faa738))
* ensure tag exists for GitHub release ([7a4679d](https://github.com/xnoto/opencode-agent-hub/commit/7a4679d05c7d1fdaa340a44a82d45898d907e51e))


### Documentation

* remove PyPI install ([970a0bf](https://github.com/xnoto/opencode-agent-hub/commit/970a0bf93ded83f8f48b1f34108c48a43d5447f8))

## [0.3.0](https://github.com/xnoto/opencode-agent-hub/compare/v0.2.1...v0.3.0) (2026-01-22)


### Features

* auto-update Homebrew tap ([fa7550a](https://github.com/xnoto/opencode-agent-hub/commit/fa7550a3e07a0c6ac704ba0164cb69fd4299df6a))


### Bug Fixes

* pass tag to publish workflow ([6928c91](https://github.com/xnoto/opencode-agent-hub/commit/6928c91c599f81ca24f4ff76d4379d479d568dda))
* pass tag to publish workflow ([f25878d](https://github.com/xnoto/opencode-agent-hub/commit/f25878d9d614d77686123993602a0c486277283b))

## [0.2.1](https://github.com/xnoto/opencode-agent-hub/compare/v0.2.0...v0.2.1) (2026-01-22)


### Bug Fixes

* derive version from package metadata ([b42a6b3](https://github.com/xnoto/opencode-agent-hub/commit/b42a6b3a02d0044a5132d344949c492a427214bc))
* read version from package metadata ([58267a3](https://github.com/xnoto/opencode-agent-hub/commit/58267a334a78111176d5cb3cc596da5d52d4fe20))
* run CI for bot PRs ([e4bbd6a](https://github.com/xnoto/opencode-agent-hub/commit/e4bbd6aeec80343e2a65e30b95cda99550f9c07c))
* run CI for github-actions PRs ([54f1af4](https://github.com/xnoto/opencode-agent-hub/commit/54f1af43c8f5245be12b6f3846336d0bedfb6de9))
* satisfy ruff import order ([1143f60](https://github.com/xnoto/opencode-agent-hub/commit/1143f60287a7697561905cffba4c40f772c982b7))

## [0.2.0](https://github.com/xnoto/opencode-agent-hub/compare/v0.1.0...v0.2.0) (2026-01-22)


### Features

* add coordinator session management ([4f99edf](https://github.com/xnoto/opencode-agent-hub/commit/4f99edfed1e4a9d254f6cffa20373b914f424579))
* initial implementation of opencode-agent-hub ([95ec85b](https://github.com/xnoto/opencode-agent-hub/commit/95ec85bc6e10cc1dfc5c26cd7823e1f49b0ceb6c))
* initial implementation of opencode-agent-hub ([68de63d](https://github.com/xnoto/opencode-agent-hub/commit/68de63d703e66634b1d392091ab2ce9aa8b21c6f))
* only orient sessions created after daemon starts ([a8aac92](https://github.com/xnoto/opencode-agent-hub/commit/a8aac92d8a77587686ce5826c0354ed9198d2a8c))


### Bug Fixes

* allow publish workflow id-token ([2b7cef6](https://github.com/xnoto/opencode-agent-hub/commit/2b7cef61df272235bb2663f297862595e0b42f3d))
* allow publish workflow id-token ([cbce5b2](https://github.com/xnoto/opencode-agent-hub/commit/cbce5b2442b38900b55e847dd87d295cbb79476a))
* run CI for release-please PRs ([4b34e25](https://github.com/xnoto/opencode-agent-hub/commit/4b34e2513f1ec8a4634c98fdc7fe262677a5c01c))
* run CI for release-please PRs ([0a5e621](https://github.com/xnoto/opencode-agent-hub/commit/0a5e621067eca65cd75d1af4435e1eff20d165c3))
* update release-please action ([456fe25](https://github.com/xnoto/opencode-agent-hub/commit/456fe25831696c3c1f616e8a747a14d7079ab3e2))
* update release-please workflow ([1964e01](https://github.com/xnoto/opencode-agent-hub/commit/1964e01036f4f584fb64bc08f1c4ee2d8a63ad4c))


### Documentation

* add complete Prometheus metrics documentation ([234b43b](https://github.com/xnoto/opencode-agent-hub/commit/234b43b6fd7581ebe3981fcbce27a96e6ec19408))
* overhaul README coordination and install ([f443c77](https://github.com/xnoto/opencode-agent-hub/commit/f443c7717b45153364f467d4fe8c1caeb3a80693))
* update coordination test results ([f08ac69](https://github.com/xnoto/opencode-agent-hub/commit/f08ac6957981f7224c7ac25c5de04798e048bfec))

## 0.1.0 (2026-01-22)


### Features

* add coordinator session management ([4f99edf](https://github.com/xnoto/opencode-agent-hub/commit/4f99edfed1e4a9d254f6cffa20373b914f424579))
* initial implementation of opencode-agent-hub ([95ec85b](https://github.com/xnoto/opencode-agent-hub/commit/95ec85bc6e10cc1dfc5c26cd7823e1f49b0ceb6c))
* initial implementation of opencode-agent-hub ([68de63d](https://github.com/xnoto/opencode-agent-hub/commit/68de63d703e66634b1d392091ab2ce9aa8b21c6f))
* only orient sessions created after daemon starts ([a8aac92](https://github.com/xnoto/opencode-agent-hub/commit/a8aac92d8a77587686ce5826c0354ed9198d2a8c))


### Bug Fixes

* allow publish workflow id-token ([2b7cef6](https://github.com/xnoto/opencode-agent-hub/commit/2b7cef61df272235bb2663f297862595e0b42f3d))
* allow publish workflow id-token ([cbce5b2](https://github.com/xnoto/opencode-agent-hub/commit/cbce5b2442b38900b55e847dd87d295cbb79476a))
* run CI for release-please PRs ([4b34e25](https://github.com/xnoto/opencode-agent-hub/commit/4b34e2513f1ec8a4634c98fdc7fe262677a5c01c))
* run CI for release-please PRs ([0a5e621](https://github.com/xnoto/opencode-agent-hub/commit/0a5e621067eca65cd75d1af4435e1eff20d165c3))
* update release-please action ([456fe25](https://github.com/xnoto/opencode-agent-hub/commit/456fe25831696c3c1f616e8a747a14d7079ab3e2))
* update release-please workflow ([1964e01](https://github.com/xnoto/opencode-agent-hub/commit/1964e01036f4f584fb64bc08f1c4ee2d8a63ad4c))


### Documentation

* add complete Prometheus metrics documentation ([234b43b](https://github.com/xnoto/opencode-agent-hub/commit/234b43b6fd7581ebe3981fcbce27a96e6ec19408))
* overhaul README coordination and install ([f443c77](https://github.com/xnoto/opencode-agent-hub/commit/f443c7717b45153364f467d4fe8c1caeb3a80693))
* update coordination test results ([f08ac69](https://github.com/xnoto/opencode-agent-hub/commit/f08ac6957981f7224c7ac25c5de04798e048bfec))
