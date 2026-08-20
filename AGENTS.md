# Agent notes

## Expo SDK 57 — verify before writing native code

APIs change between Expo SDKs. Before writing code against an Expo module, check the
**versioned** docs at https://docs.expo.dev/versions/v57.0.0/ (not the latest/unversioned
pages). Confirmed for this project:

- Entry point is `expo-router/entry` (set in `package.json` `main`).
- `babel-preset-expo` auto-configures expo-router and react-native-reanimated — no manual
  babel plugins needed (see `babel.config.js`).
- Native permission strings are declared via config plugins in `app.json`.

## Package installs

React 19 peer ranges can conflict on this SDK. Native modules install cleanly with
`npx expo install <pkg>`; JS-only packages may need `npm install --legacy-peer-deps`.

<!-- BEGIN: personal-status -->
# ~/Personal STATUS.md

- This repository lives under `/Users/greg/Personal`.
- Session start: open `/Users/greg/Personal/STATUS.md`, find this folder, continue from **Next**. Do not rediscover the project from README if Next is clear.
- Session end (even if not asked): rewrite `/Users/greg/Personal/STATUS.md` — update only this project's block and the top indexes; never delete other projects; never commit STATUS.md here.
- Protocol: `/Users/greg/Personal/AGENTS.md`
<!-- END: personal-status -->
