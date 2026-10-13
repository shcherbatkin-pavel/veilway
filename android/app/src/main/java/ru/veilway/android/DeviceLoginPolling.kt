package ru.veilway.android

import java.io.IOException
import javax.net.ssl.SSLException
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive

/** Keep the original device grant and deadline across transient network failures. */
internal suspend fun pollDeviceLogin(
    deadline: Long,
    initialInterval: Int,
    now: () -> Long,
    wait: suspend (Long) -> Unit,
    exchange: suspend () -> String,
    retrying: (Boolean) -> Unit,
): String? {
    var interval = initialInterval.coerceAtLeast(5)
    while (now() < deadline) {
        wait(interval * 1000L)
        currentCoroutineContext().ensureActive()
        if (now() >= deadline) return null
        try {
            val token = exchange()
            currentCoroutineContext().ensureActive()
            return token
        } catch (error: ApiFailure) {
            when {
                error.code == "authorization_pending" -> retrying(false)
                error.code == "slow_down" -> interval = (interval + 5).coerceAtMost(60)
                error.status == 429 -> interval = 60
                error.status in 500..599 -> {
                    interval = (interval * 2).coerceAtMost(60)
                    retrying(true)
                }
                else -> throw error
            }
        } catch (error: IOException) {
            currentCoroutineContext().ensureActive()
            if (error is SSLException) throw error
            interval = (interval * 2).coerceAtMost(60)
            retrying(true)
        }
    }
    return null
}
