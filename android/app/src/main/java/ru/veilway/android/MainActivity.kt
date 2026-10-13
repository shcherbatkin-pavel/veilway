package ru.veilway.android

import android.Manifest
import android.content.Intent
import android.net.VpnService
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.browser.customtabs.CustomTabsIntent
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.core.net.toUri

class MainActivity : ComponentActivity() {
    private val model: MainViewModel by viewModels()
    private val permission = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) {
        if (it.resultCode == RESULT_OK) connect()
    }
    private val notifications = registerForActivityResult(ActivityResultContracts.RequestPermission()) {}
    private fun connect() {
        val state = model.state.value
        if (!state.confirmed || state.selected.isEmpty() || state.profileId == null || TunnelStatus.state.value.active) return
        val intent = VpnService.prepare(this)
        if (intent != null) { permission.launch(intent); return }
        if (Build.VERSION.SDK_INT >= 33) notifications.launch(Manifest.permission.POST_NOTIFICATIONS)
        ContextCompat.startForegroundService(this, Intent(this, VeilwayVpnService::class.java).setAction("connect")
            .putExtra("profile", state.profileId).putExtra("apps", state.selected.toTypedArray()))
    }
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme(colorScheme = lightColorScheme(primary = Color(0xFF6246C7), background = Color(0xFFF6F5FA))) {
                val state by model.state.collectAsState()
                val tunnel by TunnelStatus.state.collectAsState()
                var appsScreen by remember { mutableStateOf(false) }
                var search by remember { mutableStateOf("") }
                Scaffold(topBar = { Column(Modifier.padding(24.dp)) {
                    Text("VEILWAY", style = MaterialTheme.typography.titleLarge, color = MaterialTheme.colorScheme.primary)
                    Text("Ваш интернет. Ваш выбор.", style = MaterialTheme.typography.bodyMedium)
                } }) { padding ->
                    LazyColumn(Modifier.fillMaxSize().padding(padding).padding(horizontal = 24.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
                        if (!state.loggedIn) {
                            item { Text("VPN, который не нужно выключать", style = MaterialTheme.typography.headlineMedium) }
                            item { Text("Выберите приложения для VPN. Остальные будут работать через обычное подключение.") }
                            if (state.loginCode == null) item { Button(onClick = model::beginLogin, enabled = !state.busy, modifier = Modifier.fillMaxWidth()) { Text("Войти в Veilway") } }
                            else {
                                item { Card { Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                                    Text("Код входа", style = MaterialTheme.typography.titleMedium)
                                    Text(state.loginCode!!, style = MaterialTheme.typography.headlineLarge)
                                    Text("Откройте панель, войдите через Google и подтвердите этот код. Затем вернитесь сюда.")
                                    Button(onClick = { state.browserUri?.let { CustomTabsIntent.Builder().build().launchUrl(this@MainActivity, it.toUri()) } }) { Text("Открыть вход") }
                                    TextButton(onClick = model::cancelLogin) { Text("Отмена") }
                                } } }
                            }
                        } else if (appsScreen) {
                            item { Text("Приложения для VPN", style = MaterialTheme.typography.headlineSmall) }
                            item { Text("Только отмеченные приложения используют VPN. Изменения применятся при следующем подключении.") }
                            item { OutlinedTextField(search, { search = it }, label = { Text("Поиск приложений") }, modifier = Modifier.fillMaxWidth()) }
                            item { Row(verticalAlignment = Alignment.CenterVertically) {
                                Button(onClick = { model.confirmApps(); appsScreen = false }, enabled = state.selected.isNotEmpty()) { Text("Сохранить выбор (${state.selected.size})") }
                                TextButton(onClick = { appsScreen = false }) { Text("Назад") }
                            } }
                            items(state.apps.filter { it.name.contains(search, ignoreCase = true) }
                                .sortedByDescending { it.id in state.selected }, key = { it.id }) { app ->
                                Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                                    Image(app.icon.asImageBitmap(), contentDescription = null, modifier = Modifier.size(36.dp))
                                    Text(app.name, modifier = Modifier.weight(1f).padding(horizontal = 12.dp))
                                    Checkbox(app.id in state.selected, { model.toggleApp(app.id) })
                                }
                            }
                        } else {
                            item { Card(Modifier.fillMaxWidth()) { Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                                Text(tunnel.text, style = MaterialTheme.typography.titleLarge)
                                Text("Через VPN: ${state.selected.size} приложений. Остальные — напрямую.")
                                if (tunnel.active) Button(onClick = { startService(Intent(this@MainActivity, VeilwayVpnService::class.java).setAction("disconnect")) }, modifier = Modifier.fillMaxWidth()) { Text("Отключить") }
                                else Button(onClick = { connect() }, enabled = state.confirmed && state.selected.isNotEmpty() && state.profileId != null && !state.busy, modifier = Modifier.fillMaxWidth()) { Text("Подключить") }
                            } } }
                            item { OutlinedButton(onClick = { appsScreen = true }, modifier = Modifier.fillMaxWidth()) { Text(if (state.confirmed) "Изменить приложения" else "Выбрать и подтвердить приложения") } }
                            item { Text("Ваши подключения", style = MaterialTheme.typography.titleMedium) }
                            if (state.profiles.isEmpty()) item { Text("Нет назначенных профилей. Доступ выдаёт администратор Veilway.") }
                            items(state.profiles, key = { it.id }) { profile ->
                                Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                                    RadioButton(profile.id == state.profileId, { model.selectProfile(profile.id) }, enabled = profile.usable && !tunnel.active)
                                    Column { Text(profile.name); Text(if (profile.usable) profile.mode else "Недоступен", style = MaterialTheme.typography.bodySmall) }
                                }
                            }
                            item { TextButton(onClick = model::refresh, enabled = !state.busy) { Text("Обновить подключения") } }
                            item { Text("При обрыве VPN выбранные приложения ждут восстановления. Остановка службы или явное отключение снимают защиту.", style = MaterialTheme.typography.bodySmall) }
                            item { TextButton(onClick = model::logout) { Text("Выйти и удалить доступ с телефона") } }
                        }
                        if (state.busy) item { LinearProgressIndicator(Modifier.fillMaxWidth()) }
                        if (state.message.isNotEmpty()) item { Text(state.message, color = MaterialTheme.colorScheme.error) }
                        item { Spacer(Modifier.height(24.dp)) }
                    }
                }
            }
        }
    }
}
