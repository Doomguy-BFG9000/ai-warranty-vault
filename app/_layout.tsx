import { QueryClientProvider } from '@tanstack/react-query';
import { Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { ActivityIndicator, StyleSheet, View } from 'react-native';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { AuthProvider, useAuth } from '@/hooks/useAuth';
import { usePushRegistration } from '@/hooks/usePushRegistration';
import { queryClient } from '@/lib/queryClient';
import { colors } from '@/theme';

/**
 * Route guard. `Stack.Protected` keeps the signed-in and signed-out route trees
 * mutually exclusive, so there is no window in which a signed-out user can
 * reach the vault (and no redirect flicker on cold start).
 */
function RootNavigator() {
  const { session, initializing } = useAuth();

  // Registers the device and stores its push token on the profile, which is
  // where the reminder cron looks. No-ops until someone is signed in.
  usePushRegistration();

  // Hold the UI until the persisted session has been read, otherwise a returning
  // user sees the sign-in screen for a frame before being bounced to the vault.
  if (initializing) {
    return (
      <View style={styles.loading}>
        <ActivityIndicator color={colors.primary} size="large" />
      </View>
    );
  }

  const isSignedIn = !!session;

  return (
    <Stack
      screenOptions={{
        headerStyle: { backgroundColor: colors.background },
        headerTintColor: colors.text,
        headerShadowVisible: false,
        contentStyle: { backgroundColor: colors.background },
      }}
    >
      <Stack.Protected guard={isSignedIn}>
        <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
        <Stack.Screen
          name="review"
          options={{ title: 'Confirm details', presentation: 'modal' }}
        />
        {/*
          headerBackTitle is set explicitly because the back label otherwise
          derives from the previous route's name — which for the tab navigator
          is the group folder, so the button reads "(tabs)".
        */}
        <Stack.Screen
          name="product/[id]"
          options={{ title: 'Product', headerBackTitle: 'Vault' }}
        />
        <Stack.Screen
          name="product/edit/[id]"
          options={{ title: 'Edit details', headerBackTitle: 'Back' }}
        />
        <Stack.Screen
          name="product/documents/[id]"
          options={{ title: 'Documents', headerBackTitle: 'Product' }}
        />
        <Stack.Screen
          name="product/ask/[id]"
          options={{ title: 'Ask', headerBackTitle: 'Product' }}
        />
        {/*
          A cited source is reachable from a chat message, a search result and a
          draft, so it is a top-level route rather than nested under any of them.
        */}
        <Stack.Screen
          name="document/[id]"
          options={{ title: 'Source', headerBackTitle: 'Back' }}
        />
        <Stack.Screen
          name="claim/[threadId]"
          options={{ title: 'Service request', presentation: 'modal' }}
        />
      </Stack.Protected>

      <Stack.Protected guard={!isSignedIn}>
        <Stack.Screen name="(auth)" options={{ headerShown: false }} />
      </Stack.Protected>
    </Stack>
  );
}

export default function RootLayout() {
  return (
    <GestureHandlerRootView style={styles.flex}>
      <SafeAreaProvider>
        <QueryClientProvider client={queryClient}>
          <AuthProvider>
            <StatusBar style="light" />
            <RootNavigator />
          </AuthProvider>
        </QueryClientProvider>
      </SafeAreaProvider>
    </GestureHandlerRootView>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  loading: {
    flex: 1,
    backgroundColor: colors.background,
    alignItems: 'center',
    justifyContent: 'center',
  },
});
