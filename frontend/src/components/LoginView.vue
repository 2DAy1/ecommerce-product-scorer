<script setup lang="ts">
import { ref } from "vue";

defineProps<{ loading: boolean; error: string }>();
const emit = defineEmits<{ submit: [username: string, password: string] }>();

const username = ref("");
const password = ref("");

function submit(): void {
  emit("submit", username.value.trim(), password.value);
}
</script>

<template>
  <main class="login-shell">
    <section class="login-card" aria-labelledby="login-title">
      <div class="brand-mark" aria-hidden="true">ES</div>
      <p class="eyebrow">Ecommerce intelligence</p>
      <h1 id="login-title">Product Scorer</h1>
      <p class="muted login-intro">
        Sign in to collect product signals, manage historical winners, and review
        deterministic opportunity scores.
      </p>

      <form class="form-stack" @submit.prevent="submit">
        <label>
          <span>Username</span>
          <input v-model="username" name="username" autocomplete="username" required />
        </label>
        <label>
          <span>Password</span>
          <input
            v-model="password"
            name="password"
            type="password"
            autocomplete="current-password"
            required
          />
        </label>
        <p v-if="error" class="alert alert-error" role="alert">{{ error }}</p>
        <button class="button button-primary button-wide" type="submit" :disabled="loading">
          {{ loading ? "Signing in…" : "Sign in" }}
        </button>
      </form>

      <p class="demo-note">
        Demo environment: <code>demo</code> / <code>demo12345</code>
      </p>
    </section>
  </main>
</template>
