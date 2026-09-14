# PR #817 Settings inventory and migration map

This document is the implementation inventory for the second-pass Settings information architecture in PR #817. It records where every pre-redesign Settings surface moved and which existing preference or native bridge still owns its behavior.

## Information architecture

The Settings rail has seven task-oriented destinations:

1. General
2. Models & Routing
3. Connections
4. Usage & Budgets
5. Safety & Privacy
6. Appearance
7. Advanced

The former Overview dashboard is intentionally removed. Settings now opens at General, while cross-cutting status remains next to the control it describes.

## Page migration

| Previous destination | New destination | New subsection | Compatibility route |
| --- | --- | --- | --- |
| Overview | General | Defaults for new tasks | `#settings/overview` -> `#settings/general` |
| Models & Routing | Models & Routing | Routing preference; Models | `#settings/models` |
| Providers & Connections | Connections | Provider connections | `#settings/providers` -> `#settings/connections` |
| Credits & Balance | Usage & Budgets | Provider balances | `#settings/balance` -> `#settings/usage` |
| Cost Firewall | Usage & Budgets; Safety & Privacy | Budgets & limits; Cloud boundaries | `#settings/firewall` -> `#settings/usage` |
| Model Usage | Usage & Budgets | Provider usage | `#settings/usage` |
| Permissions & Safety | Safety & Privacy | Agent permissions | `#settings/permissions` -> `#settings/safety` |
| Privacy & Data | Safety & Privacy | Data & privacy | `#settings/privacy` -> `#settings/safety` |
| Appearance | Appearance | Appearance | `#settings/appearance` |
| Tools & Insights | Advanced | Tools & Insights | `#settings/tools` -> `#settings/advanced` |
| About | Advanced | About & updates | `#settings/about` -> `#settings/advanced` |

Legacy routes remain aliases only. New navigation writes canonical hashes.

## Control inventory

### General

| Surface | Control or status | Existing owner | Disposition |
| --- | --- | --- | --- |
| Defaults for new tasks | Default run mode | `savePref("default_mode")` | Moved from Models & Routing; behavior unchanged |
| Defaults for new tasks | Task focus | `savePref("default_task_mode")` | Moved from Models & Routing; behavior unchanged |
| Defaults for new tasks | Output format | `savePref("default_output_format")` | Moved from Models & Routing; behavior unchanged |

### Models & Routing

| Surface | Control or status | Existing owner | Disposition |
| --- | --- | --- | --- |
| Routing preference | Current routing profile | Settings payload `firewall.profile` | Read-only status retained |
| Models | Default model | `savePref("default_model")` | Retained and reflected in the composer |
| Models | Model visibility toggles | `saveModelOverrides` | Retained |
| Models | Custom provider, model ID, label, and capability | `saveModelOverrides` | Retained; responsive form layout added |
| Models | Add custom model | `saveModelOverrides` | Retained |
| Models | Remove custom model | `saveModelOverrides` | Retained |
| Models | Reset model picker | `resetModelOverrides` | Retained |
| Models | Availability and validation messages | Model catalog and override payload | Read-only truth retained |
| Routing | Local-first route order | Settings payload `firewall.local_first` | Read-only status retained |

### Connections

| Surface | Control or status | Existing owner | Disposition |
| --- | --- | --- | --- |
| Connection Doctor | Overall provider health summary | Live account/provider health events | Retained and kept live after catalog refreshes |
| Connection Doctor | Account CLI presence, version, credential source, last check, and diagnostic | Settings payload and discovery events | Retained; no credential values rendered |
| Connection Doctor | Test account connection | `testAccount` | Retained |
| Connection Doctor | Sign in to account | `loginAccount` | Retained |
| Connection Doctor | Disconnect account | `disconnectAccount` | Retained with inline confirmation |
| Connection Doctor | Repair Codex configuration | `repairCodexConfig` | Retained with confirmation |
| Connection Doctor | Test API provider | `testProviderCredential` | Retained |
| CLI accounts | Connect CLI accounts | `connectAccounts` | Retained |
| GitHub | Connect personal access token | `connectGitHub` | Retained; secret input is cleared after submission |
| GitHub | Enable or disable pushes and pull requests | `setGitHubPush` | Retained |
| GitHub | Disconnect | `disconnectGitHub` | Retained |
| Free model API keys | Connect or replace provider key | `saveProviderCredential` | Retained; secret is never echoed back |
| Free model API keys | Test provider | `testProviderCredential` | Retained |
| Free model API keys | Remove provider key | `deleteProviderCredential` | Retained |
| Data boundary | What OPai can access | Existing local-data policy copy | Retained beside connection controls |

### Usage & Budgets

| Surface | Control or status | Existing owner | Disposition |
| --- | --- | --- | --- |
| Current spend | Spent today and this month | Local ledger in Settings payload | Merged from Cost Firewall; read-only estimates retained |
| Budgets & limits | Daily, monthly, and per-task caps with remaining amounts | Firewall payload | Merged from Cost Firewall; read-only status retained |
| Per-model limits | Local usage progress and source | Usage payload | Retained |
| Per-model limits | Soft limit input and save | `saveUsageLimit` | Retained |
| Provider usage | Official provider allowance, reset window, freshness, and status | Provider usage payload | Merged from Model Usage; truth labels retained |
| Provider usage | OPai-tracked calls, tasks, and tokens | Local usage payload | Retained and kept visually distinct from official data |
| Provider usage | Refresh live usage | `refreshProviderUsage` | Retained |
| Provider usage | Official usage link | Native external-link bridge | Retained |
| Provider balances | Balance, source, freshness, and recharge guidance | Provider balance payload | Merged from Credits & Balance |
| Provider balances | Manual amount and currency | `saveProviderBalance` | Retained |
| Provider balances | Refresh live balances | `refreshProviderBalances` | Retained |

### Safety & Privacy

| Surface | Control or status | Existing owner | Disposition |
| --- | --- | --- | --- |
| Cloud boundaries | Local-only mode | `setPanic` | Moved from Cost Firewall; behavior unchanged |
| Cloud boundaries | Paid cloud request policy | Firewall payload `cloud_gate` | Moved from Cost Firewall; read-only truth retained |
| Agent permissions | Current run mode and summary | Settings permission payload | Retained |
| Agent permissions | Bypass permissions | `setBypassPermissions` | Retained |
| Agent permissions | Tool permission matrix | Settings permission payload | Retained |
| Agent permissions | Run-mode comparison | `modePermissions` payload | Retained |
| Data & privacy | Local storage and telemetry statements | Privacy payload | Merged from Privacy & Data |
| Data & privacy | Clear previous chats and recents | `clearRecents` | Retained as destructive action |

### Appearance

| Surface | Control or status | Existing owner | Disposition |
| --- | --- | --- | --- |
| Appearance | Composer style | Composer preference bridge | Retained; toolbar, single line, and command bar |
| Appearance | Response detail | `savePref("response_density")` | Retained |
| Appearance | Density | `savePref("density")` | Retained |
| Appearance | Reduced motion | `savePref("reduced_motion")` | Retained |
| Appearance | Copy activity | `savePref("activity_copy")` | Retained |
| Appearance | Theme: Light, Viber Coder, Dark, Vesta, System | `savePref("theme")`, stored app-wide in `~/.opai/gui_theme.json` by `opai/gui_theme.py` | New; replaces the read-only "Dark (default)" status. Viber Coder (the original look) stays the default |

Every segmented control is exposed as a radio group with one checked, tabbable option. Left/right and up/down arrow keys move and select within the group. The theme picker is the same radio group drawn as five preview tiles; each tile renders with the real tokens of the palette it names, stars included.

### Advanced

| Surface | Control or status | Existing owner | Disposition |
| --- | --- | --- | --- |
| Tools & Insights | Prompt Library | Existing top-level view | Moved from its former Settings page; navigation retained |
| Tools & Insights | Money Saved, Cost Firewall, Context Waste, Benchmark, Agents, Proof Bundle, and Workflows | Existing top-level views | Moved together; navigation retained |
| About & updates | Version and release stage | About payload | Merged from About |
| About & updates | Build identity, artifact, asset fingerprint, file count, and runtime source | About payload | Retained |
| About & updates | Update status | Canonical updater payload | Retained as a read-only projection of updater state |
| About & updates | Check now | `checkUpdate` | Retained |
| About & updates | Update now, when supported | `applyUpdate` | Retained under existing capability guard |
| About & updates | Restart now, when available | `restartForUpdate` | Retained under existing capability guard |
| About & updates | Automatic downloads | `setUpdatePolicy("automatic_downloads")` | Retained |
| About & updates | Install on quit | `setUpdatePolicy("automatic_install_on_quit")` | Retained with dependency on automatic downloads |
| About & updates | Replay tour | Existing onboarding controller | Retained |

## Search and navigation

- Search uses static labels, descriptions, destination names, category names, aliases, and historical terms. It never indexes rendered credential values or secret inputs.
- Results show a location breadcrumb in the form `Destination > subsection` and activate the exact destination or control.
- Arrow Down enters results; Arrow Up and Arrow Down move between them; Enter activates the focused result; Escape clears the query.
- A dedicated clear button and an explicit no-results state are present.
- Desktop uses a persistent rail, tablet uses a horizontal destination strip, and phone uses an index/detail flow with a sticky back control.
- Destination activation moves focus to the page heading or matched control and preserves canonical hash navigation.

## Contract boundary

This redesign changes frontend organization, interaction, accessibility semantics, responsive layout, and tests only. It does not add or alter backend endpoints, persistence formats, provider credentials, permission policy, update policy, routing policy, or usage accounting. Existing native bridge methods and preference keys remain the source of truth.

The one addition is the theme picker (Light, Viber Coder, Dark, Vesta, System). It adds a `theme` key to the existing `savePref` bridge method and a small app-wide store (`~/.opai/gui_theme.json`), so a theme chosen in one workspace holds in every workspace. Every other preference keeps its existing per-project owner. The theming contract itself is documented in [`docs/WEB_UI.md`](WEB_UI.md#themes).

## Screenshot gallery

The inspected Playwright baselines live in [`settings-gallery.spec.js-snapshots`](../opai/assets/web/__tests__/e2e/settings-gallery.spec.js-snapshots). The gallery uses deterministic fake data and contains no credential values.

### Desktop, 1440x900

- `settings-desktop-general.png`
- `settings-desktop-models-routing.png`
- `settings-desktop-connections.png`
- `settings-desktop-usage-budgets.png`
- `settings-desktop-safety-privacy.png`
- `settings-desktop-appearance.png`
- `settings-desktop-advanced.png`

### Tablet, 768x1024

- `settings-tablet-general.png`
- `settings-tablet-models-routing.png`
- `settings-tablet-connections.png`
- `settings-tablet-usage-budgets.png`

### Phone, 390x844

- `settings-phone-index.png`
- `settings-phone-search-results.png`
- `settings-phone-search-no-results.png`
- `settings-phone-models-routing.png`
- `settings-phone-connections.png`
- `settings-phone-usage-budgets.png`
- `settings-phone-safety-privacy.png`
- `settings-phone-appearance.png`

### High-information states

- `settings-state-failed-connection-recovery.png`
- `settings-state-near-budget-limit.png`
- `settings-state-permission-restricted.png`
- `settings-state-update-available.png`

### Light, Dark and Vesta themes

The baselines for the Light, Dark and Vesta palettes live in [`theme.spec.js-snapshots`](../opai/assets/web/__tests__/e2e/theme.spec.js-snapshots). The Settings gallery above shows Viber Coder, the default.

- `light-chat-finished-turn.png`
- `light-settings-appearance.png`
- `light-settings-connections.png`
- `light-settings-safety.png`
- `light-settings-phone-appearance.png`
- `dark-chat-finished-turn.png`
- `dark-settings-appearance.png`
- `dark-settings-connections.png`
- `dark-settings-safety.png`
