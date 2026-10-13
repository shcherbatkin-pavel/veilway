package ru.veilway.android

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.launch

interface MobileClient {
    suspend fun device(): DeviceLogin
    suspend fun exchange(code: String): String
    suspend fun profiles(token: String): List<Profile>
    suspend fun download(token: String, id: String): String
    suspend fun logout(token: String)
}

data class PendingLogin(val device: String, val code: String, val uri: String, val deadline: Long, val interval: Int)

interface LoginStore {
    fun token(): String?
    fun saveToken(token: String)
    fun pending(): PendingLogin?
    fun savePending(login: PendingLogin)
    fun clearPending()
}

sealed interface LoginState {
    data object Idle : LoginState
    data object Cancelled : LoginState
    data object Allocating : LoginState
    data class Awaiting(val login: PendingLogin, val retrying: Boolean = false) : LoginState
    data object Granted : LoginState
    data object Expired : LoginState
    data class Failed(val error: Exception) : LoginState
}

/** Own one grant from allocation through polling; cancelled generations cannot persist results. */
class DeviceLoginCoordinator(
    private val scope: CoroutineScope,
    private val api: MobileClient,
    private val store: LoginStore,
    private val now: () -> Long,
    private val wait: suspend (Long) -> Unit,
    private val changed: (LoginState) -> Unit,
) {
    private var job: Job? = null
    private var generation = 0

    fun begin() {
        if (job?.isActive == true) return
        val current = ++generation
        changed(LoginState.Allocating)
        job = scope.launch {
            run(current) {
                val grant = api.device()
                currentCoroutineContext().ensureActive()
                if (current != generation) return@run
                val login = PendingLogin(grant.deviceCode, grant.userCode, grant.uri, now() + grant.expires, grant.interval)
                store.savePending(login)
                poll(current, login)
            }
        }
    }

    fun resume(login: PendingLogin) {
        val current = ++generation
        job?.cancel()
        job = scope.launch { run(current) { poll(current, login) } }
    }

    fun cancel() {
        ++generation
        job?.cancel()
        store.clearPending()
        changed(LoginState.Cancelled)
    }

    private suspend fun run(current: Int, action: suspend () -> Unit) {
        try {
            action()
        } catch (error: CancellationException) {
            throw error
        } catch (error: Exception) {
            if (current == generation) changed(LoginState.Failed(error))
        } finally {
            if (current == generation) {
                store.clearPending()
                changed(LoginState.Idle)
            }
        }
    }

    private suspend fun poll(current: Int, login: PendingLogin) {
        changed(LoginState.Awaiting(login))
        val token = pollDeviceLogin(login.deadline, login.interval, now, wait,
            exchange = { api.exchange(login.device) },
            retrying = { retry -> if (current == generation) changed(LoginState.Awaiting(login, retry)) })
        if (current != generation) return
        if (token == null) changed(LoginState.Expired)
        else {
            store.saveToken(token)
            store.clearPending()
            changed(LoginState.Granted)
        }
    }
}

data class AvailableProfiles(val profiles: List<Profile>, val selectedId: String?)

/** A response from an old session must not restore profiles or invalidate a new login. */
class ProfileAccess(private val api: MobileClient, private val store: LoginStore) {
    suspend fun refresh(selectedId: () -> String? = { null }): AvailableProfiles? {
        val token = store.token() ?: throw ApiFailure(401)
        val profiles = try {
            api.profiles(token)
        } catch (error: CancellationException) {
            throw error
        } catch (error: Exception) {
            if (store.token() != token) return null
            throw error
        }
        currentCoroutineContext().ensureActive()
        if (store.token() != token) return null
        val selected = selectedId()?.takeIf { id -> profiles.any { it.id == id && it.usable } }
            ?: profiles.firstOrNull { it.usable }?.id
        return AvailableProfiles(profiles, selected)
    }
}
