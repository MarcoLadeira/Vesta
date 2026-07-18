# Issue #388 Design Tokens and Icons

## Decision

Introduce a single web token sheet loaded before component CSS. It owns colour,
type, spacing, radius, elevation, motion, and icon-size values. Component CSS
consumes those variables; a small repository-local lint script rejects raw
`font-size`, `margin`, `padding`, and `gap` pixel values outside the documented
exception list.

## Scope

- Preserve current DOM selectors and all bridge behavior.
- Move the existing visual root variables into `design-tokens.css`, extending
  them with a readable type scale, a 4px-based spacing scale, radii, and
  elevation.
- Add an inline SVG registry that uses `currentColor`, has a consistent 16px
  default, and distinguishes decorative from labelled icons.
- Replace chrome emoji/text glyphs in the header, composer, workspace chooser,
  activity/status output, and file chips.
- Add unit tests for the token lint and E2E checks for decorative SVG and both
  normal and compact density layouts.

## Guardrails

The lint intentionally does not police one-pixel borders, positional offsets,
or duration values. Those are not spacing rhythm. It rejects raw pixel values
only in typography and component layout spacing declarations, allowing a short
documented legacy exception list only when a browser-engine constraint requires
one.

## Verification

The test suite verifies token declarations and lint fixtures, icons render as
SVG with `aria-hidden` when decorative, and desktop/compact screenshots preserve
the composer and workspace controls. The existing full browser suite protects
stable selectors.
