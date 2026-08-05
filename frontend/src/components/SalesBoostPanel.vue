<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import { api, ApiError } from "../api";
import type { PaginatedResponse, SuccessfulProduct } from "../types";

const records = ref<PaginatedResponse<SuccessfulProduct> | null>(null);
const loading = ref(false);
const listError = ref("");
const title = ref("");
const category = ref("");
const keywords = ref("");
const manualLoading = ref(false);
const manualMessage = ref("");
const manualError = ref("");
const fieldErrors = ref<Record<string, string>>( {} );
const selectedFile = ref<File | null>(null);
const fileInput = ref<HTMLInputElement | null>(null);
const uploadLoading = ref(false);
const uploadMessage = ref("");
const uploadError = ref("");

const canSubmitManual = computed(
  () => Boolean(title.value.trim() && category.value.trim()) && !manualLoading.value,
);

function validationErrors(error: ApiError): Record<string, string> {
  const result: Record<string, string> = {};
  for (const [field, value] of Object.entries(error.payload || {})) {
    if (Array.isArray(value)) result[field] = value.map(String).join(" ");
    else if (typeof value === "string") result[field] = value;
  }
  return result;
}

async function load(path = "/api/sales-boost/"): Promise<void> {
  loading.value = true;
  listError.value = "";
  try {
    records.value = await api.getSuccessfulProducts(path);
  } catch (caught) {
    listError.value = caught instanceof ApiError ? caught.message : "Unable to load Sales Boost records.";
  } finally {
    loading.value = false;
  }
}

function normalizedKeywords(): string[] {
  const seen = new Set<string>();
  return keywords.value
    .split(/[;,]/)
    .map((value) => value.trim())
    .filter((value) => {
      const key = value.toLocaleLowerCase();
      if (!value || seen.has(key)) return false;
      seen.add(key);
      return true;
    });
}

async function submitManual(): Promise<void> {
  if (!canSubmitManual.value) return;
  manualLoading.value = true;
  manualMessage.value = "";
  manualError.value = "";
  fieldErrors.value = {};
  try {
    await api.createSuccessfulProduct({
      title: title.value.trim(),
      category: category.value.trim(),
      keywords: normalizedKeywords(),
    });
    manualMessage.value = "Sales Boost record saved.";
    title.value = "";
    category.value = "";
    keywords.value = "";
    await load();
  } catch (caught) {
    if (caught instanceof ApiError) {
      manualError.value = caught.message;
      fieldErrors.value = validationErrors(caught);
    } else {
      manualError.value = "Unable to save the record.";
    }
  } finally {
    manualLoading.value = false;
  }
}

function selectFile(event: Event): void {
  selectedFile.value = (event.target as HTMLInputElement).files?.[0] || null;
  uploadMessage.value = "";
  uploadError.value = "";
}

async function uploadCsv(): Promise<void> {
  if (!selectedFile.value || uploadLoading.value) return;
  uploadLoading.value = true;
  uploadMessage.value = "";
  uploadError.value = "";
  try {
    const result = await api.importSuccessfulProducts(selectedFile.value);
    uploadMessage.value = `Import complete: ${result.created_count} created, ${result.updated_count} updated.`;
    selectedFile.value = null;
    if (fileInput.value) fileInput.value.value = "";
    await load();
  } catch (caught) {
    uploadError.value = caught instanceof ApiError ? caught.message : "Unable to import the CSV file.";
  } finally {
    uploadLoading.value = false;
  }
}

onMounted(() => void load());
</script>

<template>
  <section class="panel sales-panel" aria-labelledby="sales-heading">
    <div class="section-heading">
      <div>
        <p class="eyebrow">Historical signal</p>
        <h2 id="sales-heading">Sales Boost library</h2>
        <p class="muted">Maintain examples of historically successful products.</p>
      </div>
      <button class="button button-secondary button-small" type="button" :disabled="loading" @click="load()">
        {{ loading ? "Refreshing…" : "Refresh" }}
      </button>
    </div>

    <div class="sales-grid">
      <div class="subpanel">
        <h3>Saved records</h3>
        <p v-if="listError" class="alert alert-error">{{ listError }}</p>
        <div v-if="loading && !records" class="empty-state">Loading records…</div>
        <div v-else-if="records && records.results.length" class="compact-list">
          <article v-for="record in records.results" :key="record.id" class="boost-record">
            <div>
              <strong>{{ record.title }}</strong>
              <span>{{ record.category }}</span>
            </div>
            <div class="tag-row">
              <span v-for="keyword in record.keywords" :key="keyword" class="tag">{{ keyword }}</span>
              <span v-if="record.keywords.length === 0" class="secondary">No keywords</span>
            </div>
          </article>
        </div>
        <div v-else class="empty-state">No historical products uploaded.</div>
        <div v-if="records" class="pagination-row compact-pagination">
          <span>{{ records.count }} records</span>
          <div>
            <button class="button button-secondary button-small" :disabled="!records.previous || loading" @click="records.previous && load(records.previous)">Previous</button>
            <button class="button button-secondary button-small" :disabled="!records.next || loading" @click="records.next && load(records.next)">Next</button>
          </div>
        </div>
      </div>

      <div class="forms-column">
        <form class="subpanel form-stack" @submit.prevent="submitManual">
          <h3>Add or update manually</h3>
          <label>
            <span>Title</span>
            <input v-model="title" required maxlength="500" />
            <small v-if="fieldErrors.title" class="field-error">{{ fieldErrors.title }}</small>
          </label>
          <label>
            <span>Category</span>
            <input v-model="category" required maxlength="255" />
            <small v-if="fieldErrors.category" class="field-error">{{ fieldErrors.category }}</small>
          </label>
          <label>
            <span>Keywords</span>
            <input v-model="keywords" placeholder="wireless; audio; travel" />
            <small>Separate keywords with semicolons or commas.</small>
            <small v-if="fieldErrors.keywords" class="field-error">{{ fieldErrors.keywords }}</small>
          </label>
          <p v-if="manualMessage" class="alert alert-success">{{ manualMessage }}</p>
          <p v-if="manualError" class="alert alert-error">{{ manualError }}</p>
          <button class="button button-primary" type="submit" :disabled="!canSubmitManual">
            {{ manualLoading ? "Saving…" : "Save record" }}
          </button>
        </form>

        <form class="subpanel form-stack" @submit.prevent="uploadCsv">
          <h3>Import CSV</h3>
          <p class="helper-code"><code>title,category,keywords</code></p>
          <p class="muted small-text">Use UTF-8 CSV and semicolon-separated keywords.</p>
          <label class="file-picker">
            <span>CSV file</span>
            <input ref="fileInput" type="file" accept=".csv,text/csv" @change="selectFile" />
          </label>
          <p class="selected-file">{{ selectedFile?.name || "No file selected" }}</p>
          <p v-if="uploadMessage" class="alert alert-success">{{ uploadMessage }}</p>
          <p v-if="uploadError" class="alert alert-error">{{ uploadError }}</p>
          <button class="button button-primary" type="submit" :disabled="!selectedFile || uploadLoading">
            {{ uploadLoading ? "Uploading…" : "Upload CSV" }}
          </button>
        </form>
      </div>
    </div>
  </section>
</template>
