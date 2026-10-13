package ru.veilway.android

import android.app.Application
import android.content.Intent
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
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
    private var polling: Job? = null
    private var loginGeneration = 0
    init {
        viewModelScope.launch {
            try {
                val apps = withContext(Dispatchers.IO) { InstalledApps(getApplication()).load() }
                val saved = store.get("apps")?.let { data -> JSONArray(data).let { a -> (0 until a.length()).map { a.getString(it) }.toSet() } }
                val selected = (saved ?: setOf("com.google.android.youtube", "org.telegram.messenger")).intersect(apps.map { it.id }.toSet())
                state.update { it.copy(apps = apps, selected = selected, confirmed = saved != null,
                    loggedIn = store.get("token") != null, profileId = store.get("selected-profile")) }
                if (state.value.loggedIn) refresh()
                else store.get("login")?.let { resumeLogin(JSONObject(it)) }
            } catch (_: Exception) { state.update { it.copy(message = "Не удалось прочитать настройки. Войдите заново.") } }
        }
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
            val token = store.get("token") ?: throw ApiFailure(401)
            val profiles = api.profiles(token)
            val id = state.value.profileId?.takeIf { selected -> profiles.any { it.id == selected && it.usable } }
                ?: profiles.firstOrNull { it.usable }?.id
            state.update { it.copy(profiles = profiles, profileId = id) }
        } catch (error: Exception) {
            if (error is ApiFailure && error.status == 401) {
                getApplication<Application>().stopService(Intent(getApplication(), VeilwayVpnService::class.java))
                store.clear(); state.update { it.copy(loggedIn = false, profiles = emptyList()) }
            }
            state.update { it.copy(message = failure(error)) }
        } finally { state.update { it.copy(busy = false) } }
    } }
    fun beginLogin() { viewModelScope.launch {
        if (state.value.busy || polling?.isActive == true) return@launch
        state.update { it.copy(busy = true, message = "") }
        try {
            val login = api.device()
            val data = JSONObject().put("device", login.deviceCode).put("code", login.userCode)
                .put("uri", login.uri).put("deadline", Instant.now().epochSecond + login.expires).put("interval", login.interval)
            store.put("login", data.toString()); resumeLogin(data)
        } catch (error: Exception) { state.update { it.copy(message = failure(error)) } }
        finally { state.update { it.copy(busy = false) } }
    } }
    private fun resumeLogin(data: JSONObject) {
        val generation = ++loginGeneration
        polling?.cancel()
        state.update { it.copy(loginCode = data.getString("code"), browserUri = data.getString("uri")) }
        polling = viewModelScope.launch {
            try {
                val token = pollDeviceLogin(
                    deadline = data.getLong("deadline"), initialInterval = data.getInt("interval"),
                    now = { Instant.now().epochSecond }, wait = { delay(it) },
                    exchange = { api.exchange(data.getString("device")) },
                    retrying = { retry ->
                        if (generation == loginGeneration) state.update { it.copy(message =
                            if (retry) "Связь с Veilway прервалась. Повторяем попытку; код входа сохраняется." else "") }
                    },
                )
                if (generation != loginGeneration) return@launch
                if (token == null) {
                    state.update { it.copy(message = "Код истёк. Начните вход заново.") }
                } else {
                    store.put("token", token); store.remove("login")
                    state.update { it.copy(loggedIn = true, loginCode = null, browserUri = null, message = "") }
                    refresh()
                }
            } catch (error: CancellationException) {
                throw error
            } catch (error: Exception) {
                if (generation == loginGeneration) state.update { it.copy(message = failure(error)) }
            } finally {
                if (generation == loginGeneration) {
                    store.remove("login"); state.update { it.copy(loginCode = null, browserUri = null) }
                }
            }
        }
    }
    fun cancelLogin() {
        ++loginGeneration
        polling?.cancel(); store.remove("login")
        state.update { it.copy(loginCode = null, browserUri = null, busy = false) }
    }
    fun selectProfile(id: String) { store.put("selected-profile", id); state.update { it.copy(profileId = id) } }
    fun toggleApp(id: String) { state.update { it.copy(selected = if (id in it.selected) it.selected - id else it.selected + id, confirmed = false) } }
    fun confirmApps() {
        if (state.value.selected.isEmpty()) return
        store.put("apps", JSONArray(state.value.selected.sorted()).toString())
        state.update { it.copy(confirmed = true) }
    }
    fun logout() {
        ++loginGeneration
        polling?.cancel()
        val context = getApplication<Application>()
        context.stopService(Intent(context, VeilwayVpnService::class.java))
        val token = runCatching { store.get("token") }.getOrNull()
        store.clear()
        state.update { it.copy(loggedIn = false, profiles = emptyList(), loginCode = null, browserUri = null, message = "", confirmed = false) }
        viewModelScope.launch {
            if (token != null) try { api.logout(token) }
            catch (_: Exception) { state.update { it.copy(message = "Данные удалены с телефона. Сервер недоступен: сессия на сервере пока не отозвана.") } }
        }
    }
}
