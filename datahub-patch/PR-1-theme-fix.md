# PR 1 of 2 — file this one first

**Title**

```
fix(ui): correct theme import path in useSetAppTheme
```

**Target:** `datahub-project/datahub` `master`
**Branch:** one commit, `a79de83` — do not bundle with PR 2, both are squash-merged

---

## Body

`useSetAppTheme.tsx` lives in `src/app/` and dynamically imports
`` `./conf/theme/${customThemeId}` ``, but the themes live at `src/conf/theme/`.
From that module the path needs `../`.

esbuild's dependency scan cannot resolve the glob and fails the dev server boot
outright:

```
Could not resolve import('./conf/theme/**/*')
```

So `vite` will not start at all when a custom theme id is configured. One
character.

```diff
-                import(/* @vite-ignore */ `./conf/theme/${customThemeId}`)
+                import(/* @vite-ignore */ `../conf/theme/${customThemeId}`)
```

### Checklist

- [x] The PR conforms to DataHub's Contributing Guideline (particularly PR Title Format)
- [ ] Links to related issues — none filed; happy to open one first if preferred
- [ ] Tests for the changes have been added/updated — not applicable, this is a build-time module resolution fix with no runtime branch to assert on
- [ ] Docs related to the changes have been added/updated — not applicable
- [ ] For any breaking change/potential downtime/deprecation/big changes an entry has been made in Updating DataHub — not applicable, no behaviour change

### Notes for the reviewer

The `assets/conf/theme/...` fetch fallback further down the same file is already
correct — only the dev-mode dynamic import has the wrong prefix, which is why
this only bites under `vite` and not a production build.
