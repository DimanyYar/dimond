# 0006. Plain CSS with design tokens, no build step

Status: Accepted, 2026-10-15

## Context

The UI kit already defines colours, type, spacing, radii and states. Tailwind was considered:
it needs Node.js and a CSS build in development and deploy.

## Decision

One stylesheet with the UI-kit tokens as CSS custom properties and named component classes
(`.btn-primary`, `.chip`, `.card`). No npm, no CSS build.

## Consequences

+ No Node.js in the stack; the design system is visible in one file; HTML stays readable.
− Utility-first speed is lost. Tailwind can be added later without rewriting templates at
once.
