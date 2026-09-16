---
name: refactor-codemod
description: Use when refactoring or applying repetitive code transformations.
---
# Refactor Codemod
Prefer AST-aware tools such as ast-grep for structural edits. Do a dry-run first, verify with tests, keep each change reviewable, and avoid unrelated cleanup so the diff stays revertible.
