# Icon sources

`app-icon.svg` is the master for every app icon: a shield with a check mark, in the app's
theme colours (`src/theme`). The PNGs in `assets/` are rendered from it:

| File | Size | Notes |
| --- | --- | --- |
| `icon.png` | 1024 | iOS / default. Full-bleed and opaque; the OS applies the rounded mask. |
| `android-icon-foreground.png` | 512 | Mark scaled into the adaptive-icon safe zone, transparent. |
| `android-icon-background.png` | 512 | Background gradient only. |
| `android-icon-monochrome.png` | 432 | Alpha-only silhouette for Android 13 themed icons. |
| `favicon.png` | 48 | Web. |
| `splash-icon.png` | 1024 | Mark only, transparent. |
| `images/shield.png` | 157×192 | The mark cropped to the shield, for the in-app logo (`src/components/ShieldLogo.tsx`). |

`social-preview.html` lays out `docs/social-preview.png` (1280×640, the image GitHub shows
when the repository link is shared) around the README screenshots in `docs/screenshots/`.
The same script renders it, so retaking those screenshots and re-running the script
refreshes it; upload the result under **Settings → General → Social preview**.

To regenerate after editing the SVG, the card or the screenshots (needs a local puppeteer and Chromium; neither is
a project dependency):

```bash
PUPPETEER=/path/to/node_modules/puppeteer CHROMIUM=/path/to/chromium \
  node assets/source/render-icons.mjs
```
