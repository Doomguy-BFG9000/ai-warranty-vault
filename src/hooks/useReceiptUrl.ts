import { useQuery } from '@tanstack/react-query';

import { getReceiptSignedUrl } from '@/lib/receipts';

const SIGNED_URL_TTL_SECONDS = 3600;

/**
 * A short-lived signed URL for a stored receipt.
 *
 * The bucket is private, so there is no permanent URL to render. The cache is
 * kept comfortably shorter than the signature's own lifetime, so a URL is never
 * handed to <Image> after it has already expired.
 */
export function useReceiptUrl(path: string | null | undefined) {
  return useQuery({
    queryKey: ['receipt-url', path],
    enabled: !!path,
    queryFn: () => getReceiptSignedUrl(path!, SIGNED_URL_TTL_SECONDS),
    staleTime: (SIGNED_URL_TTL_SECONDS - 300) * 1000,
    gcTime: (SIGNED_URL_TTL_SECONDS - 300) * 1000,
    retry: 1,
  });
}
