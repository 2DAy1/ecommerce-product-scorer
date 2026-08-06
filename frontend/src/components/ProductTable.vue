<script setup lang="ts">
import { ref } from "vue";

import type { PaginatedResponse, Product } from "../types";

defineProps<{
  page: PaginatedResponse<Product> | null;
  loading: boolean;
  error: string;
}>();

defineEmits<{ navigate: [path: string] }>();

const money = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 2,
});
const integer = new Intl.NumberFormat("en-US");
const failedImages = ref<Record<number, boolean>>({});

function markImageFailed(productId: number): void {
  failedImages.value[productId] = true;
}

function formatMoney(value: string | null): string {
  return value === null ? "—" : money.format(Number(value));
}

function formatScore(value: string): string {
  return Number(value).toFixed(2);
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("en", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}
</script>

<template>
  <div v-if="error" class="alert alert-error" role="alert">{{ error }}</div>
  <div v-if="loading && !page" class="empty-state">Loading products…</div>
  <div v-else-if="page && page.results.length === 0" class="empty-state">
    <strong>No products collected yet.</strong>
    <span>Run Amazon collection to populate the catalog.</span>
  </div>
  <div v-else-if="page" class="table-wrap" :aria-busy="loading">
    <table class="data-table product-table">
      <thead>
        <tr>
          <th>Product</th>
          <th>Category</th>
          <th>Amazon signals</th>
          <th>Keyword</th>
          <th>Latest analysis</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="product in page.results" :key="product.id">
          <td class="product-cell">
            <div class="product-summary">
              <div class="product-thumbnail">
                <img
                  v-if="product.image_url && !failedImages[product.id]"
                  :src="product.image_url"
                  :alt="`${product.title} thumbnail`"
                  loading="lazy"
                  referrerpolicy="no-referrer"
                  @error="markImageFailed(product.id)"
                />
                <span v-else class="product-image-fallback">No image</span>
              </div>
              <div class="product-copy">
                <a
                  v-if="product.product_url"
                  class="product-title"
                  :href="product.product_url"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {{ product.title }}
                </a>
                <span v-else class="product-title">{{ product.title }}</span>
                <span class="secondary">ASIN {{ product.asin }}</span>
              </div>
            </div>
          </td>
          <td>{{ product.category || "—" }}</td>
          <td>
            <div class="signal-list">
              <span>{{ formatMoney(product.price) }}</span>
              <span>{{ product.rating === null ? "No rating" : `${product.rating} ★` }}</span>
              <span>{{ integer.format(product.reviews_count) }} reviews</span>
            </div>
          </td>
          <td>{{ product.search_keyword || "—" }}</td>
          <td class="analysis-cell">
            <template v-if="product.latest_analysis">
              <div class="score-line">
                <span class="score-pill">
                  {{ formatScore(product.latest_analysis.final_score) }}
                </span>
                <span class="source-badge">{{ product.latest_analysis.provider }}</span>
              </div>
              <div class="component-row">
                <span>Amazon {{ formatScore(product.latest_analysis.baseline_score) }}</span>
                <span>Trends {{ formatScore(product.latest_analysis.trend_score) }}</span>
                <span>Boost {{ formatScore(product.latest_analysis.boost_score) }}</span>
              </div>
              <details>
                <summary>View recommendation</summary>
                <p>{{ product.latest_analysis.reasoning }}</p>
                <small>{{ formatDate(product.latest_analysis.calculated_at) }}</small>
              </details>
            </template>
            <span v-else class="not-analyzed">Not analyzed</span>
          </td>
        </tr>
      </tbody>
    </table>
  </div>

  <div v-if="page" class="pagination-row">
    <span>{{ page.count }} products</span>
    <div>
      <button
        class="button button-secondary button-small"
        type="button"
        :disabled="!page.previous || loading"
        @click="page.previous && $emit('navigate', page.previous)"
      >
        Previous
      </button>
      <button
        class="button button-secondary button-small"
        type="button"
        :disabled="!page.next || loading"
        @click="page.next && $emit('navigate', page.next)"
      >
        Next
      </button>
    </div>
  </div>
</template>
