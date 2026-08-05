<script setup lang="ts">
import { onMounted, ref } from "vue";

import { api, ApiError } from "../api";
import { useJobPolling } from "../composables/useJobPolling";
import type { JobRun, JobType, PaginatedResponse, Product } from "../types";
import ProductTable from "./ProductTable.vue";
import SalesBoostPanel from "./SalesBoostPanel.vue";

defineProps<{ username: string; loggingOut: boolean }>();
defineEmits<{ logout: [] }>();

const products = ref<PaginatedResponse<Product> | null>(null);
const productsLoading = ref(false);
const productsError = ref("");

const jobLabels: Record<JobType, string> = {
  product_collection: "Amazon collection",
  trend_collection: "Google Trends collection",
  product_analysis: "Product analysis",
};

async function loadProducts(path = "/api/products/"): Promise<void> {
  productsLoading.value = true;
  productsError.value = "";
  try {
    products.value = await api.getProducts(path);
  } catch (caught) {
    productsError.value = caught instanceof ApiError ? caught.message : "Unable to load products.";
  } finally {
    productsLoading.value = false;
  }
}

async function handleTerminal(job: JobRun): Promise<void> {
  if (job.status === "succeeded") {
    await loadProducts();
  }
}

const polling = useJobPolling(handleTerminal);

function launch(jobType: JobType): void {
  void polling.launch(jobType);
}

function progress(job: JobRun): number {
  if (!job.total_items) return job.status === "succeeded" ? 100 : 0;
  return Math.min(100, ((job.processed_items + job.failed_items) / job.total_items) * 100);
}

onMounted(() => void loadProducts());
</script>

<template>
  <div class="app-shell">
    <header class="app-header">
      <div class="brand-lockup">
        <div class="brand-mark brand-mark-small">ES</div>
        <div>
          <strong>Ecommerce Product Scorer</strong>
          <span>Opportunity dashboard</span>
        </div>
      </div>
      <div class="header-actions">
        <span class="user-chip"><span class="user-dot"></span>{{ username }}</span>
        <button class="button button-secondary button-small" type="button" :disabled="productsLoading" @click="loadProducts()">
          {{ productsLoading ? "Refreshing…" : "Refresh" }}
        </button>
        <button class="button button-ghost button-small" type="button" :disabled="loggingOut" @click="$emit('logout')">
          {{ loggingOut ? "Signing out…" : "Sign out" }}
        </button>
      </div>
    </header>

    <main class="dashboard">
      <section class="hero-panel">
        <div>
          <p class="eyebrow">Research workflow</p>
          <h1>Find products worth a closer look.</h1>
          <p>
            Collect marketplace and demand signals, compare them with historical
            winners, then generate a transparent 0–100 opportunity score.
          </p>
        </div>
        <div class="catalog-stat">
          <span>Catalog size</span>
          <strong>{{ products?.count ?? "—" }}</strong>
          <small>products available</small>
        </div>
      </section>

      <section class="panel workflow-panel" aria-labelledby="workflow-heading">
        <div class="section-heading">
          <div>
            <p class="eyebrow">Collection & analysis</p>
            <h2 id="workflow-heading">Run the pipeline</h2>
          </div>
          <span v-if="polling.active.value" class="live-indicator">Job in progress</span>
        </div>

        <div class="job-actions">
          <button class="action-card" type="button" :disabled="polling.active.value" @click="launch('product_collection')">
            <span class="action-number">01</span>
            <strong>Collect Amazon products</strong>
            <small>Refresh catalog and marketplace signals</small>
          </button>
          <button class="action-card" type="button" :disabled="polling.active.value" @click="launch('trend_collection')">
            <span class="action-number">02</span>
            <strong>Collect Google Trends</strong>
            <small>Capture current demand and growth</small>
          </button>
          <button class="action-card action-card-accent" type="button" :disabled="polling.active.value" @click="launch('product_analysis')">
            <span class="action-number">03</span>
            <strong>Analyze products</strong>
            <small>Calculate scores and recommendations</small>
          </button>
        </div>

        <p v-if="polling.error.value" class="alert alert-error" role="alert">{{ polling.error.value }}</p>
        <div v-if="polling.job.value" class="job-status-card">
          <div class="job-status-head">
            <div>
              <span class="status-badge" :data-status="polling.job.value.status">
                {{ polling.job.value.status }}
              </span>
              <strong>{{ jobLabels[polling.job.value.job_type] }}</strong>
            </div>
            <span>{{ polling.job.value.processed_items }} / {{ polling.job.value.total_items }} processed</span>
          </div>
          <div class="progress-track" aria-hidden="true">
            <span :style="{ width: `${progress(polling.job.value)}%` }"></span>
          </div>
          <div class="job-meta">
            <span>{{ polling.job.value.failed_items }} failed</span>
            <code>{{ polling.job.value.id }}</code>
          </div>
          <p v-if="polling.job.value.error_message" class="job-error">
            {{ polling.job.value.error_message }}
          </p>
        </div>
      </section>

      <section class="panel" aria-labelledby="products-heading">
        <div class="section-heading">
          <div>
            <p class="eyebrow">Current catalog</p>
            <h2 id="products-heading">Products & latest scores</h2>
            <p class="muted">Results use backend pagination and the latest saved analysis only.</p>
          </div>
          <button class="button button-secondary button-small" type="button" :disabled="productsLoading" @click="loadProducts()">
            Refresh products
          </button>
        </div>
        <ProductTable
          :page="products"
          :loading="productsLoading"
          :error="productsError"
          @navigate="loadProducts"
        />
      </section>

      <SalesBoostPanel />
    </main>

    <footer class="app-footer">
      <span>Ecommerce Product Scorer MVP</span>
      <span>Deterministic scoring works without an external LLM account.</span>
    </footer>
  </div>
</template>
