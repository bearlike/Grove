"use client";

import { useMutation, useQuery, type UseMutationResult, type UseQueryResult } from "@tanstack/react-query";
import { useQueryClient } from "@tanstack/react-query";

import type {
  UsageActivityView,
  UsageBashInsightView,
  UsageBreakdownView,
  UsageFindingsView,
  UsageQuotasView,
  UsageRefreshView,
  UsageSeriesView,
  UsageSessionPageView,
  UsageSummaryView,
} from "@/lib/grove/api";
import { groveClient } from "./client";
import { groveKeys } from "./keys";

/**
 * The historical usage audit — the past-tense sibling of the activity stream.
 *
 * Every endpoint takes the same opaque filter map (`since`, `until`, `timezone`,
 * `provider`, `account`, `project`, `model`, …), so it stays a
 * `Record<string, string>` rather than a typed shape this layer would have to
 * keep in step with the daemon's query parameters. The page owns the vocabulary;
 * these hooks only carry it.
 *
 * Nothing here polls: this is history, and it only changes when the derived
 * cache is refreshed.
 */

export type UsageFilters = Record<string, string>;

export function useUsageSummary(filters: UsageFilters): UseQueryResult<UsageSummaryView> {
  return useQuery({
    queryKey: groveKeys.usageSummary(filters),
    queryFn: () => groveClient.getUsageSummary(filters),
  });
}

export function useUsageActivity(
  filters: UsageFilters,
  metric: string,
): UseQueryResult<UsageActivityView> {
  return useQuery({
    queryKey: groveKeys.usageActivity(filters, metric),
    queryFn: () => groveClient.getUsageActivity(filters, metric),
  });
}

export function useUsageSessions(
  filters: UsageFilters,
  options: { sort: string; limit: number; cursor?: string | null },
): UseQueryResult<UsageSessionPageView> {
  return useQuery({
    queryKey: groveKeys.usageSessions(filters, options.sort, options.cursor ?? null),
    queryFn: () => groveClient.getUsageSessions(filters, options),
  });
}

export function useUsageBreakdown(
  filters: UsageFilters,
  dimension: string,
): UseQueryResult<UsageBreakdownView> {
  return useQuery({
    queryKey: groveKeys.usageBreakdown(filters, dimension),
    queryFn: () => groveClient.getUsageBreakdown(filters, dimension),
  });
}

/**
 * One metric per day, split by one dimension — a breakdown that kept its days.
 *
 * `dimension` and `metric` are part of the key rather than a refetch of one
 * entry, so flipping the card's dropdown back to a grouping already fetched is
 * instant and the previous chart is never blanked while the new one loads.
 */
export function useUsageSeries(
  filters: UsageFilters,
  dimension: string,
  metric: string,
): UseQueryResult<UsageSeriesView> {
  return useQuery({
    queryKey: groveKeys.usageSeries(filters, dimension, metric),
    queryFn: () => groveClient.getUsageSeries(filters, dimension, metric),
  });
}

/** The subscription quotas the daemon is configured to inspect. An empty set
 * means no credential is allowed, not that there is nothing to show. */
export function useUsageQuotas(): UseQueryResult<UsageQuotasView> {
  return useQuery({
    queryKey: groveKeys.usageQuotas,
    queryFn: () => groveClient.getUsageQuotas(),
  });
}

export function useUsageFindings(filters: UsageFilters): UseQueryResult<UsageFindingsView> {
  return useQuery({
    queryKey: groveKeys.usageFindings(filters),
    queryFn: () => groveClient.getUsageFindings(filters),
  });
}

/** Which processes eat the most agent time, ranked by total Bash-call duration. */
export function useUsageBashCommands(
  filters: UsageFilters,
): UseQueryResult<UsageBashInsightView> {
  return useQuery({
    queryKey: groveKeys.usageBashCommands(filters),
    queryFn: () => groveClient.getUsageBashCommands(filters),
  });
}

/**
 * Rebuild the derived usage cache, then re-read everything under it.
 *
 * `meta.toast: false` opts out of the global mutation toast (see
 * `components/grove/providers.tsx`). This is the one mutation in the app whose
 * failure is already a statement about the data on screen rather than an event:
 * the coverage strip prints "Refresh failed; the indexed data above is
 * unchanged", which is the sentence a toast would repeat with less context.
 */
export function useRefreshUsage(): UseMutationResult<UsageRefreshView, Error, void> {
  const queryClient = useQueryClient();
  return useMutation({
    meta: { toast: false },
    mutationFn: () => groveClient.refreshUsage(),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: groveKeys.usage });
    },
  });
}
