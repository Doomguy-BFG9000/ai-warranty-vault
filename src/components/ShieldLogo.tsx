import { Image, type ImageStyle, type StyleProp } from 'react-native';

// Rendered from assets/source/app-icon.svg, the same master as the app icon, so the
// in-app logo and the home-screen icon always match.
const SHIELD = require('../../assets/images/shield.png');
const ASPECT_RATIO = 572 / 700;

type Props = {
  /** Height in points; the width follows the shield's proportions. */
  size: number;
  style?: StyleProp<ImageStyle>;
};

/** The app's shield mark. Decorative: screen readers skip it. */
export function ShieldLogo({ size, style }: Props) {
  return (
    <Image
      source={SHIELD}
      style={[{ width: size * ASPECT_RATIO, height: size }, style]}
      resizeMode="contain"
      accessible={false}
      accessibilityIgnoresInvertColors
    />
  );
}
