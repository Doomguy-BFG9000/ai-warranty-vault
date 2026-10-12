import Ionicons from '@expo/vector-icons/Ionicons';
import { Tabs } from 'expo-router';
import type { ComponentProps } from 'react';
import { StyleSheet } from 'react-native';

import { ShieldLogo } from '@/components/ShieldLogo';
import { colors, typography } from '@/theme';

type IconName = ComponentProps<typeof Ionicons>['name'];
type IconColor = ComponentProps<typeof Ionicons>['color'];

const ICON_SIZE = 22;

/**
 * The Vault tab uses the app's own shield mark; the others use Ionicons, filled when
 * selected and outlined otherwise, in the tab bar's active and inactive tints.
 */
function TabIcon({
  name,
  focused,
  color,
}: {
  name: IconName;
  focused: boolean;
  color: IconColor;
}) {
  return (
    <Ionicons
      name={focused ? name : (`${name}-outline` as IconName)}
      size={ICON_SIZE}
      color={color}
    />
  );
}

export default function TabsLayout() {
  return (
    <Tabs
      screenOptions={{
        headerStyle: { backgroundColor: colors.background },
        headerTintColor: colors.text,
        headerShadowVisible: false,
        headerTitleStyle: typography.heading,
        tabBarStyle: {
          backgroundColor: colors.surface,
          borderTopColor: colors.border,
        },
        tabBarActiveTintColor: colors.primary,
        tabBarInactiveTintColor: colors.textFaint,
        sceneStyle: { backgroundColor: colors.background },
      }}
    >
      <Tabs.Screen
        name="index"
        options={{
          title: 'Vault',
          tabBarIcon: ({ focused }) => (
            <ShieldLogo size={ICON_SIZE} style={!focused && styles.iconInactive} />
          ),
        }}
      />
      <Tabs.Screen
        name="add"
        options={{
          title: 'Add',
          tabBarIcon: ({ focused, color }) => (
            <TabIcon name="camera" focused={focused} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="search"
        options={{
          title: 'Search',
          tabBarIcon: ({ focused, color }) => (
            <TabIcon name="search" focused={focused} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="settings"
        options={{
          title: 'Settings',
          tabBarIcon: ({ focused, color }) => (
            <TabIcon name="settings" focused={focused} color={color} />
          ),
        }}
      />
    </Tabs>
  );
}

const styles = StyleSheet.create({
  iconInactive: { opacity: 0.5 },
});
