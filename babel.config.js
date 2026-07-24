module.exports = function (api) {
  api.cache(true);
  return {
    // babel-preset-expo already wires up expo-router and the
    // react-native-reanimated/worklets plugin automatically in SDK 57.
    presets: ['babel-preset-expo'],
  };
};
