package ru.veilway.android

import android.app.Application
import android.content.Intent
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import java.time.Instant

data class AppState(
    val loggedIn: Boolean = false, val busy: Boolean = false, val message: String = "",
    val profiles: List<Profile> = emptyList(), val profileId: String? = null,
    val apps: List<InstalledApp> = emptyList(), val selected: Set<String> = emptySet(),
    val confirmed: Boolean = false, val loginCode: String? = null, val browserUri: String? = null,
)

class MainViewModel(application: Application) : AndroidViewModel(application) {
    private val store = SecretStore(application)
    private val api = MobileApi()
    val state = MutableStateFlow(AppState())
    private val loginStore = StoredLogin(store)
    private val access = ProfileAccess(api, loginStore)
    private val login = DeviceLoginCoordinator(viewModelScope, api, loginStore,
        now = { Instant.now().epochSecond }, wait = { delay(it) }, changed = ::loginChanged)
    init {
        viewModelScope.launch {
            try {
                val apps = withContext(Dispatchers.IO) { InstalledApps(getApplication()).load() }
                val saved = store.get("apps")?.let { data -> JSONArray(data).let { a -> (0 until a.length()).map { a.getString(it) }.toSet() } }
                val selected = (saved ?: setOf("com.google.android.youtube", "org.telegram.messenger")).intersect(apps.map { it.id }.toSet())
                state.update { it.copy(apps = apps, selected = selected, confirmed = saved != null,
                    loggedIn = store.get("token") != null, profileId = store.get("selected-profile")) }
                if (state.value.loggedIn) refresh()
                else loginStore.pending()?.let(login::resume)
            } catch (_: Exception) { state.update { it.copy(message = "Не удалось прочитать настройки. Войдите заново.") } }
        }
    }
    private fun loginChanged(next: LoginState) {
        state.update { current -> when (next) {
            LoginState.Idle -> current.copy(loginCode = null, browserUri = null)
            LoginState.Cancelled -> current.copy(loginCode = null, browserUri = null, busy = false)
            LoginState.Allocating -> current.copy(busy = true, message = "")
            is LoginState.Awaiting -> current.copy(busy = false, loginCode = next.login.code, browserUri = next.login.uri,
                message = if (next.retrying) "Связь с Veilway прервалась. Повторяем попытку; код входа сохраняется." else "")
            LoginState.Granted -> current.copy(loggedIn = true, loginCode = null, browserUri = null, message = "")
            LoginState.Expired -> current.copy(message = "Код истёк. Начните вход заново.")
            is LoginState.Failed -> current.copy(busy = false, message = failure(next.error))
        } }
        if (next == LoginState.Granted) refresh()
    }
    private fun failure(error: Exception): String = when (error) {
        is ApiFailure -> when {
            error.code == "expired_token" -> "Код входа истёк или уже использован. Начните вход заново."
            error.code == "access_denied" -> "Вход не разрешён. Обратитесь к администратору."
            error.status == 401 -> "Срок входа истёк. Войдите заново."
            error.status == 429 -> "Слишком много запросов. Подождите и повторите."
            else -> "Сервис временно недоступен. Попробуйте позже."
        }
        is javax.net.ssl.SSLException -> "Не удалось установить защищённое соединение с Veilway. Проверьте дату и время устройства."
        is org.json.JSONException -> "Сервер вернул неверный ответ. Попробуйте войти заново."
        is java.io.IOException -> "Не удалось связаться с Veilway. Проверьте интернет."
        else -> "Не удалось завершить операцию. Попробуйте снова."
    }
    fun refresh() { viewModelScope.launch {
        state.update { it.copy(busy = true, message = "") }
        try {
            val result = access.refresh { state.value.profileId } ?: return@launch
            state.update { it.copy(profiles = result.profiles, profileId = result.selectedId) }
        } catch (error: CancellationException) {
            throw error
        } catch (error: Exception) {
            if (error is ApiFailure && error.status == 401) {
                login.cancel()
                stopVpn()
                store.clear(); state.update { it.copy(loggedIn = false, profiles = emptyList()) }
            }
            state.update { it.copy(message = failure(error)) }
        } finally { state.update { it.copy(busy = false) } }
    } }
    fun beginLogin() { if (!state.value.busy) login.begin() }
    fun cancelLogin() = login.cancel()
    fun selectProfile(id: String) { store.put("selected-profile", id); state.update { it.copy(profileId = id) } }
    fun toggleApp(id: String) { state.update { it.copy(selected = if (id in it.selected) it.selected - id else it.selected + id, confirmed = false) } }
    fun confirmApps() {
        if (state.value.selected.isEmpty()) return
        store.put("apps", JSONArray(state.value.selected.sorted()).toString())
        state.update { it.copy(confirmed = true) }
    }
    fun logout() {
        login.cancel()
        stopVpn()
        val token = runCatching { store.get("token") }.getOrNull()
        store.clear()
        state.update { it.copy(loggedIn = false, profiles = emptyList(), loginCode = null, browserUri = null, message = "", confirmed = false) }
        viewModelScope.launch {
            if (token != null) try { api.logout(token) }
            catch (_: Exception) { state.update { it.copy(message = "Данные удалены с телефона. Сервер недоступен: сессия на сервере пока не отозвана.") } }
        }
    }
    private fun stopVpn() {
        val context = getApplication<Application>()
        val service = Intent(context, VeilwayVpnService::class.java)
        context.stopService(service)
    }
}
