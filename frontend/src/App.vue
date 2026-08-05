<script setup lang="ts">
import { onMounted, ref } from "vue";

import { api, ApiError, setUnauthorizedHandler } from "./api";
import DashboardView from "./components/DashboardView.vue";
import LoginView from "./components/LoginView.vue";
import type { SessionState } from "./types";

const session = ref<SessionState>({ authenticated: false, username: "" });
const checkingSession = ref(true);
const loginLoading = ref(false);
const logoutLoading = ref(false);
const loginError = ref("");

function resetSession(): void {
  session.value = { authenticated: false, username: "" };
}

async function checkSession(): Promise<void> {
  checkingSession.value = true;
  loginError.value = "";
  try {
    session.value = await api.getSession();
  } catch (caught) {
    resetSession();
    loginError.value = caught instanceof ApiError ? caught.message : "Unable to reach the application.";
  } finally {
    checkingSession.value = false;
  }
}

async function signIn(username: string, password: string): Promise<void> {
  if (loginLoading.value) return;
  loginLoading.value = true;
  loginError.value = "";
  try {
    session.value = await api.login(username, password);
  } catch (caught) {
    loginError.value = caught instanceof ApiError ? caught.message : "Unable to sign in.";
  } finally {
    loginLoading.value = false;
  }
}

async function signOut(): Promise<void> {
  if (logoutLoading.value) return;
  logoutLoading.value = true;
  try {
    await api.logout();
  } catch {
    // Close the authenticated view even when the server session already expired.
  } finally {
    resetSession();
    logoutLoading.value = false;
  }
}

setUnauthorizedHandler(resetSession);
onMounted(checkSession);
</script>

<template>
  <main v-if="checkingSession" class="loading-shell">
    <div class="loading-mark" aria-hidden="true"></div>
    <p>Opening dashboard…</p>
  </main>
  <DashboardView
    v-else-if="session.authenticated"
    :username="session.username"
    :logging-out="logoutLoading"
    @logout="signOut"
  />
  <LoginView v-else :loading="loginLoading" :error="loginError" @submit="signIn" />
</template>
