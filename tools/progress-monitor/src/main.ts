import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { createRouter, createWebHashHistory } from 'vue-router'
import App from './App.vue'
import ChangeList from './views/ChangeList.vue'
import ChangeDetail from './views/ChangeDetail.vue'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', name: 'list', component: ChangeList },
    { path: '/change/:name', name: 'detail', component: ChangeDetail },
  ],
})

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.mount('#app')