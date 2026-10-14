package ru.veilway.android

import java.time.Instant
import java.util.Properties
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Test

class ClientContractTest {
    private val contract = Properties().apply {
        val stream = requireNotNull(ClientContractTest::class.java.getResourceAsStream("/client-contract.properties"))
        stream.use { load(it) }
    }
    private fun values(key: String) = requireNotNull(contract.getProperty(key)).split(",")

    @Test fun profileModesAndStatusesRetainAccessPolicy() {
        for (mode in values("profile.modes")) {
            for (status in values("profile.statuses") + "unknown-synthetic-status") {
                val profile = Profile("synthetic", "test", mode, status, Instant.now().plusSeconds(3600))
                assertEquals(mode, profile.mode)
                assertEquals(status == "active", profile.usable)
            }
            assertFalse(Profile("synthetic", "test", mode, "active", Instant.EPOCH).usable)
        }
    }

    @Test fun pendingAndSlowDownKeepGrantAndAdjustInterval() = runBlocking {
        var now = 0L
        var calls = 0
        val waits = mutableListOf<Long>()
        val result = pollDeviceLogin(600, 5, { now }, { waits += it; now += it / 1000 }, {
            when (++calls) {
                1 -> throw ApiFailure(400, contract.getProperty("mobile.pending"))
                2 -> throw ApiFailure(400, contract.getProperty("mobile.slow_down"))
                else -> "synthetic-token"
            }
        }, {})
        assertEquals("synthetic-token", result)
        assertEquals(listOf(5000L, 5000L, 10000L), waits)
    }

    @Test fun terminalAndUnknownErrorsEndPolling() {
        for (code in values("mobile.terminal") + "unknown-synthetic-error") {
            val failure = ApiFailure(400, code)
            var calls = 0
            val caught = runCatching {
                runBlocking { pollDeviceLogin(600, 5, { 0 }, {}, { calls++; throw failure }, {}) }
            }.exceptionOrNull()
            assertSame(failure, caught)
            assertEquals(1, calls)
        }
    }
}
