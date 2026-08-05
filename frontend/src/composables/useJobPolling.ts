import { computed, onBeforeUnmount, ref } from "vue";

import { api, ApiError } from "../api";
import type { JobRun, JobType } from "../types";

const POLL_INTERVAL_MS = 1500;

export function useJobPolling(onTerminal: (job: JobRun) => void | Promise<void>) {
  const job = ref<JobRun | null>(null);
  const launching = ref(false);
  const error = ref("");
  let timer: number | null = null;
  let generation = 0;

  const active = computed(
    () => launching.value || job.value?.status === "pending" || job.value?.status === "running",
  );

  function stop(): void {
    generation += 1;
    if (timer !== null) {
      window.clearTimeout(timer);
      timer = null;
    }
  }

  async function poll(id: string, currentGeneration: number): Promise<void> {
    if (currentGeneration !== generation) return;
    try {
      const latest = await api.getJob(id);
      if (currentGeneration !== generation) return;
      job.value = latest;
      if (latest.status === "succeeded" || latest.status === "failed") {
        await onTerminal(latest);
        return;
      }
      timer = window.setTimeout(() => void poll(id, currentGeneration), POLL_INTERVAL_MS);
    } catch (caught) {
      error.value = caught instanceof ApiError ? caught.message : "Unable to refresh job status.";
      stop();
    }
  }

  async function launch(jobType: JobType): Promise<void> {
    if (active.value) return;
    stop();
    error.value = "";
    launching.value = true;
    const currentGeneration = generation;
    try {
      job.value = await api.launchJob(jobType);
      void poll(job.value.id, currentGeneration);
    } catch (caught) {
      error.value = caught instanceof ApiError ? caught.message : "Unable to launch the job.";
    } finally {
      launching.value = false;
    }
  }

  onBeforeUnmount(stop);

  return { job, launching, active, error, launch, stop };
}
