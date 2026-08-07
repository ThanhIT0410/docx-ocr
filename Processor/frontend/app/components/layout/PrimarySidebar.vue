<script setup lang="ts">
import { Files, LayoutDashboard, Settings } from '@lucide/vue'

const route = useRoute()
const isExams = computed(() => route.path.startsWith('/exams'))
const isDashboard = computed(() => route.path.startsWith('/dashboard'))
const isSettings = computed(() => route.path.startsWith('/settings'))
</script>

<template>
  <nav class="rail" aria-label="Điều hướng chính">
    <div class="rail-logo" aria-hidden="true">PC</div>

    <NuxtLink to="/exams" class="rail-btn" :class="{ active: isExams }" title="Đề" aria-label="Đề">
      <Files :size="21" :stroke-width="1.8" />
      <span class="rail-tip">Đề</span>
    </NuxtLink>

    <NuxtLink to="/dashboard" class="rail-btn" :class="{ active: isDashboard }" title="Dashboard" aria-label="Dashboard">
      <LayoutDashboard :size="21" :stroke-width="1.8" />
      <span class="rail-tip">Dashboard</span>
    </NuxtLink>

    <span class="rail-spacer" />

    <NuxtLink to="/settings" class="rail-btn" :class="{ active: isSettings }" title="Cài đặt" aria-label="Cài đặt">
      <Settings :size="21" :stroke-width="1.8" />
      <span class="rail-tip">Cài đặt</span>
    </NuxtLink>
  </nav>
</template>

<style scoped>
.rail {
  width: 64px; flex: none; background: var(--ink);
  display: flex; flex-direction: column; align-items: center;
  padding: 14px 0; gap: 6px;
}
.rail-spacer { flex: 1; }
.rail-logo {
  width: 36px; height: 36px; border-radius: 9px; background: var(--accent);
  display: grid; place-items: center; margin-bottom: 14px;
  color: #fff; font-weight: 700; font-size: 13px; letter-spacing: .5px;
}
.rail-btn {
  width: 44px; height: 44px; border-radius: 10px; display: grid; place-items: center;
  color: #8FA0BF; position: relative; transition: background .15s, color .15s;
  text-decoration: none;
}
.rail-btn:hover { background: rgba(255, 255, 255, .08); color: #DCE4F2; }
.rail-btn.active { background: rgba(43, 79, 216, .35); color: #fff; }
.rail-btn.active::before {
  content: ""; position: absolute; left: -10px; top: 10px; bottom: 10px; width: 3px;
  border-radius: 0 3px 3px 0; background: var(--accent);
}
.rail-tip {
  position: absolute; left: 56px; top: 50%; transform: translateY(-50%);
  background: var(--ink); color: #fff; font-size: 12px; padding: 4px 9px; border-radius: 6px;
  white-space: nowrap; opacity: 0; pointer-events: none; transition: opacity .15s; z-index: 50;
  box-shadow: var(--shadow-md);
}
.rail-btn:hover .rail-tip { opacity: 1; }
</style>
