<script setup lang="ts">
import { onMounted, ref } from "vue";

type HealthState = "checking" | "healthy" | "unavailable";

const health = ref<HealthState>("checking");

async function checkBackend(): Promise<void> {
  health.value = "checking";

  try {
    const response = await fetch("/api/health/");
    const payload = (await response.json()) as { status?: string };
    health.value = response.ok && payload.status === "ok" ? "healthy" : "unavailable";
  } catch {
    health.value = "unavailable";
  }
}

onMounted(checkBackend);
</script>

<template>
  <main class="shell">
    <section class="card">
      <p class="eyebrow">Infrastructure skeleton</p>
      <h1>Parser MVP</h1>
      <p class="lead">
        Django, Vue, PostgreSQL, Redis і Celery готові до наступного етапу.
      </p>

      <div class="status-row" :data-state="health">
        <span class="status-dot" aria-hidden="true"></span>
        <span v-if="health === 'checking'">Перевіряємо backend…</span>
        <span v-else-if="health === 'healthy'">Backend працює</span>
        <span v-else>Backend недоступний</span>
      </div>

      <button type="button" @click="checkBackend">Перевірити ще раз</button>

      <p class="scope-note">
        Scraping, Google Trends, scoring і бізнес-моделі не входять до цього етапу.
      </p>
    </section>
  </main>
</template>
