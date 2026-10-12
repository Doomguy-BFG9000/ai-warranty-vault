// Warranty expiry is compared in calendar days against the device's local
// timezone, so tests are pinned to UTC to stay deterministic no matter where
// they run. Set before workers spawn so they inherit it.
process.env.TZ = 'UTC';

/** @type {import('jest').Config} */
module.exports = {
  preset: 'jest-expo',
  moduleNameMapper: {
    '^@/(.*)$': '<rootDir>/src/$1',
  },
  testMatch: ['**/__tests__/**/*.test.ts?(x)'],
  setupFiles: ['<rootDir>/test/setupEnv.js'],
};
