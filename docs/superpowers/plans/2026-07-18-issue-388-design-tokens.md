# Design Tokens and Icons Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make type, spacing, elevation, radius, and icons a documented and enforceable web UI system.

**Architecture:** `design-tokens.css` is the visual source of truth; `styles.css` consumes it. `icons.js` provides safe inline SVG fragments for chrome. A Node lint script inspects the shipped stylesheets and is run by the unit-test command and CI.

**Tech Stack:** CSS custom properties, browser-safe inline SVG, Node.js, Vitest, Playwright.

### Task 1: Token contract and enforcement

- [x] Add a failing token-fixture test asserting type, spacing, radius, elevation, and icon tokens exist and raw or mixed layout/type pixels are rejected.
- [x] Add `design-tokens.css` and move the current root visual tokens into it.
- [x] Add `scripts/lint-web-design-tokens.mjs`, wire it into a dedicated `npm run test:tokens` command and the CI web job.
- [x] Convert stylesheet typography and spacing declarations to approved token references.

### Task 2: One icon system

- [x] Add E2E coverage for inline SVG rendering and decorative accessibility behavior.
- [x] Add `vesta/assets/web/icons.js`, loaded before `app.js`.
- [x] Replace chrome emoji/text glyphs with SVG registry calls while retaining labels and selectors.
- [x] Add E2E accessibility checks plus visual snapshots for the four core surfaces at both densities.

### Task 3: Documentation and verification

- [x] Document the complete web scale and icon rules in `docs/DESIGN_SYSTEM.md`, with a renderable preview.
- [x] Run lint fixtures, unit tests, focused and full browser suites, syntax and diff checks.
- [ ] Create and merge the PR closing #388.
