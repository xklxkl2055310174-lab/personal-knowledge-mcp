import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', name: 'overview', component: () => import('./views/Overview.vue'), meta: { title: '总览' } },
  { path: '/agent', name: 'agent', component: () => import('./views/AgentRun.vue'), meta: { title: 'Agent 问答' } },
  { path: '/tools', name: 'tools', component: () => import('./views/ToolPlayground.vue'), meta: { title: '工具调试台' } },
  { path: '/files', name: 'files', component: () => import('./views/Files.vue'), meta: { title: '笔记与工作区' } },
  { path: '/:pathMatch(.*)*', redirect: '/' }
]

const router = createRouter({ history: createWebHistory(), routes })
router.afterEach(to => { document.title = `${to.meta.title || ''} · Knowledge MCP` })
export default router
