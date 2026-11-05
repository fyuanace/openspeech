import { createApp } from 'vue'
import {
  ElAlert, ElButton, ElCard, ElCheckbox, ElDialog, ElForm, ElFormItem, ElInput,
  ElOption, ElSelect, ElSwitch, ElTag,
} from 'element-plus'
import 'element-plus/es/components/alert/style/css'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/checkbox/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/form-item/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/switch/style/css'
import 'element-plus/es/components/tag/style/css'
import App from './App.vue'
import './style.css'

const app = createApp(App)
for (const component of [
  ElAlert, ElButton, ElCard, ElCheckbox, ElDialog, ElForm, ElFormItem, ElInput,
  ElOption, ElSelect, ElSwitch, ElTag,
]) app.component(component.name, component)
app.mount('#app')
