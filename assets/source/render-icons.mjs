// Renders every app icon variant from app-icon.svg with a headless Chromium.
//
//   PUPPETEER=/path/to/puppeteer CHROMIUM=/path/to/chromium node assets/source/render-icons.mjs
//
// Not a project dependency on purpose: icons change rarely, and puppeteer would add
// a browser download to every `npm ci`.
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const assets = join(here, '..');
const require = createRequire(import.meta.url);
const puppeteer = require(process.env.PUPPETEER ?? 'puppeteer');

const master = readFileSync(join(here, 'app-icon.svg'), 'utf8');
const defs = master.match(/<defs>[\s\S]*?<\/defs>/)[0];
const background = master.match(/<rect id="background"[^>]*\/>/)[0];
const mark = master.match(/<g id="mark">[\s\S]*?<\/g>/)[0];

// Android adaptive icons are cropped to a circle or squircle inside the centre ~66%,
// so the mark is scaled down around the centre to stay inside that safe zone.
const SAFE_SCALE = 0.72;
const scaled = (inner) =>
  `<g transform="translate(512 512) scale(${SAFE_SCALE}) translate(-512 -512)">${inner}</g>`;

// Monochrome (Android 13 themed icons) uses alpha only: a solid shield with the
// check knocked out.
const monochrome = `
  <mask id="cut"><rect width="1024" height="1024" fill="#fff"/>
    <path d="M396 512 L478 594 L640 432" fill="none" stroke="#000" stroke-width="64"
          stroke-linecap="round" stroke-linejoin="round"/></mask>
  <path mask="url(#cut)" fill="#fff"
        d="M512 176 L784 278 V488 C784 668 666 794 512 848 C358 794 240 668 240 488 V278 Z"/>`;

const svg = (body, viewBox = '0 0 1024 1024') =>
  `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${viewBox}" width="100%" height="100%">${defs}${body}</svg>`;

// The in-app logo: the mark alone, cropped to the shield and its stroke so it can
// sit inline at any height. Rendered at 3× its largest use (64 pt).
const SHIELD_VIEWBOX = '226 162 572 700';

const variants = [
  // iOS: full-bleed and opaque — the system applies the rounded mask itself.
  { file: 'icon.png', size: 1024, body: background + mark, transparent: false },
  { file: 'favicon.png', size: 48, body: background + mark, transparent: false },
  { file: 'splash-icon.png', size: 1024, body: mark, transparent: true },
  { file: 'android-icon-background.png', size: 512, body: background, transparent: false },
  { file: 'android-icon-foreground.png', size: 512, body: scaled(mark), transparent: true },
  { file: 'android-icon-monochrome.png', size: 432, body: scaled(monochrome), transparent: true },
  {
    file: 'images/shield.png',
    width: 157,
    height: 192,
    body: mark,
    viewBox: SHIELD_VIEWBOX,
    transparent: true,
  },
];

const browser = await puppeteer.launch({
  executablePath: process.env.CHROMIUM,
  args: ['--no-sandbox'],
});
const page = await browser.newPage();
for (const { file, size, width = size, height = size, body, viewBox, transparent } of variants) {
  await page.setViewport({ width, height, deviceScaleFactor: 1 });
  await page.setContent(
    `<html><body style="margin:0;background:transparent;width:${width}px;height:${height}px">` +
      `${svg(body, viewBox)}</body></html>`,
  );
  await page.screenshot({ path: join(assets, file), omitBackground: transparent });
  console.log(`${file} ${width}x${height}`);
}
await browser.close();
