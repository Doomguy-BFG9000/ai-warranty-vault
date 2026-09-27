// https://docs.expo.dev/guides/using-eslint/
const { defineConfig } = require('eslint/config');
const expoConfig = require('eslint-config-expo/flat');

module.exports = defineConfig([
  expoConfig,
  {
    ignores: [
      'dist/*',
      'node_modules/*',
      // Edge Functions run on Deno with its own globals and .ts import
      // specifiers; they are linted by `deno lint`, not this config.
      'supabase/functions/*',
      // Generated or tool-owned, not source we maintain.
      'supabase/.temp/*',
      // The Python service: its own toolchain, and a virtualenv full of vendored
      // JavaScript that has nothing to do with this app.
      'services/ai/.venv/*',
      'services/ai/**/__pycache__/*',
      '.expo/*',
      'ios/*',
      'android/*',
    ],
  },
]);
