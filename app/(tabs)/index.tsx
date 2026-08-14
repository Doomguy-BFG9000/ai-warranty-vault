import { router } from 'expo-router';
import { useMemo } from 'react';
import { ActivityIndicator, FlatList, RefreshControl, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { Button } from '@/components/Button';
import { ProductCard } from '@/components/ProductCard';
import { useProducts } from '@/hooks/useProducts';
import { getDatabaseErrorMessage } from '@/lib/errors';
import { getWarrantyState } from '@/lib/warranty';
import { colors, spacing, typography } from '@/theme';
import type { Product } from '@/types/models';

export default function VaultScreen() {
  const { data: products, isLoading, isError, error, refetch, isRefetching } = useProducts();

  // Products already arrive expiry-first from the query; this only counts the
  // ones that need attention so the header can say so.
  const attentionCount = useMemo(
    () =>
      (products ?? []).filter((p) => {
        const status = getWarrantyState(p.warranty_expires_at).status;
        return status === 'expiring' || status === 'expired';
      }).length,
    [products],
  );

  if (isLoading) {
    return (
      <SafeAreaView style={styles.centered} edges={['bottom']}>
        <ActivityIndicator color={colors.primary} size="large" />
      </SafeAreaView>
    );
  }

  if (isError) {
    return (
      <SafeAreaView style={styles.centered} edges={['bottom']}>
        <Text style={styles.emoji}>⚠️</Text>
        <Text style={styles.title}>Could not load your vault</Text>
        <Text style={styles.subtitle}>{getDatabaseErrorMessage(error)}</Text>
        <Button label="Try again" variant="secondary" onPress={() => void refetch()} />
      </SafeAreaView>
    );
  }

  if (!products || products.length === 0) {
    return (
      <SafeAreaView style={styles.centered} edges={['bottom']}>
        <Text style={styles.emoji}>🛡️</Text>
        <Text style={styles.title}>Your vault is empty</Text>
        <Text style={styles.subtitle}>
          Photograph a receipt and we&apos;ll pull out the product and warranty for
          you to confirm.
        </Text>
        <Button label="Add your first receipt" onPress={() => router.push('/add')} />
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.flex} edges={['bottom']}>
      <FlatList<Product>
        data={products}
        keyExtractor={(item) => item.id}
        contentContainerStyle={styles.list}
        ListHeaderComponent={
          attentionCount > 0 ? (
            <View style={styles.notice}>
              <Text style={styles.noticeText}>
                {attentionCount === 1
                  ? '1 warranty needs attention'
                  : `${attentionCount} warranties need attention`}
              </Text>
            </View>
          ) : null
        }
        renderItem={({ item }) => (
          <ProductCard
            product={item}
            onPress={() => router.push({ pathname: '/product/[id]', params: { id: item.id } })}
          />
        )}
        refreshControl={
          <RefreshControl
            refreshing={isRefetching}
            onRefresh={() => void refetch()}
            tintColor={colors.textMuted}
          />
        }
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: colors.background },
  centered: {
    flex: 1,
    backgroundColor: colors.background,
    alignItems: 'center',
    justifyContent: 'center',
    padding: spacing.xl,
    gap: spacing.md,
  },
  list: { padding: spacing.lg, gap: spacing.md },
  emoji: { fontSize: 56, marginBottom: spacing.sm },
  title: { ...typography.heading, color: colors.text, textAlign: 'center' },
  subtitle: {
    ...typography.body,
    color: colors.textMuted,
    textAlign: 'center',
    maxWidth: 300,
    lineHeight: 22,
    marginBottom: spacing.sm,
  },
  notice: {
    backgroundColor: colors.surfaceAlt,
    borderRadius: 12,
    padding: spacing.md,
    marginBottom: spacing.xs,
  },
  noticeText: { ...typography.label, color: colors.warning },
});
