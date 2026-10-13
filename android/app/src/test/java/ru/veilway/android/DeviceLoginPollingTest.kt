package ru.veilway.android

import java.net.SocketTimeoutException
import javax.net.ssl.SSLException
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Test

class DeviceLoginPollingTest {
    @Test fun temporaryFailureKeepsPollingAndReturnsToken() = runBlocking {
        var now = 0L
        var calls = 0
        val waits = mutableListOf<Long>()
        val notices = mutableListOf<Boolean>()
        val token = pollDeviceLogin(600, 5, { now }, {
            waits += it; now += it / 1000
        }, {
            when (++calls) {
                1 -> throw SocketTimeoutException()
                2 -> throw ApiFailure(503)
                3 -> throw ApiFailure(400, "authorization_pending")
                else -> "synthetic-token"
            }
        }, { notices += it })
        assertEquals("synthetic-token", token)
        assertEquals(listOf(5000L, 10000L, 20000L, 20000L), waits)
        assertEquals(listOf(true, true, false), notices)
    }
    @Test fun serverSlowDownIncreasesPollingInterval() = runBlocking {
        var now = 0L
        var calls = 0
        val waits = mutableListOf<Long>()
        pollDeviceLogin(600, 5, { now }, { waits += it; now += it / 1000 }, {
            if (++calls == 1) throw ApiFailure(400, "slow_down")
            "synthetic-token"
        }, {})
        assertEquals(listOf(5000L, 10000L), waits)
    }
    @Test fun deadlineEndsRetriesWithoutAnotherRequest() = runBlocking {
        var now = 0L
        var calls = 0
        val token = pollDeviceLogin(12, 5, { now }, { now += it / 1000 }, {
            calls++; throw SocketTimeoutException()
        }, {})
        assertNull(token)
        assertEquals(1, calls)
    }
    @Test fun terminalErrorsAndCancellationAreNotRetried() {
        for (failure in listOf(ApiFailure(400, "expired_token"), ApiFailure(400, "access_denied"), SSLException("synthetic"), CancellationException())) {
            var calls = 0
            val caught = runCatching {
                runBlocking { pollDeviceLogin(600, 5, { 0 }, {}, { calls++; throw failure }, {}) }
            }.exceptionOrNull()
            assertSame(failure, caught)
            assertEquals(1, calls)
        }
    }
    @Test fun throttledRequestsWaitForOneMinute() = runBlocking {
        var now = 0L
        var calls = 0
        val waits = mutableListOf<Long>()
        pollDeviceLogin(600, 5, { now }, { waits += it; now += it / 1000 }, {
            if (++calls == 1) throw ApiFailure(429)
            "synthetic-token"
        }, {})
        assertEquals(listOf(5000L, 60000L), waits)
    }
}
