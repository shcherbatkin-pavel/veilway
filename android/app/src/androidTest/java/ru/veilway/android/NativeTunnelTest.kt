package ru.veilway.android

import android.content.Intent
import android.net.VpnService
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import java.net.HttpURLConnection
import java.net.URL

/** Synthetic localhost server only. No production profiles or diagnostics. */
@RunWith(AndroidJUnit4::class)
class NativeTunnelTest {
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val context = instrumentation.targetContext
    private fun shell(command: String): String = instrumentation.uiAutomation.executeShellCommand(command).use {
        android.os.ParcelFileDescriptor.AutoCloseInputStream(it).bufferedReader().readText()
    }
    private fun awaitState(predicate: (String) -> Boolean) {
        val deadline = System.nanoTime() + 60_000_000_000L
        while (System.nanoTime() < deadline) {
            if (predicate(TunnelStatus.state.value.text)) return
            Thread.sleep(250)
        }
        fail("Tunnel state: " + TunnelStatus.state.value.text)
    }
    private fun control(path: String) {
        val connection = URL("http://10.0.2.2:18081/$path").openConnection() as HttpURLConnection
        connection.connectTimeout = 5000; connection.readTimeout = 5000
        try { assertEquals(200, connection.responseCode) } finally { connection.disconnect() }
    }
    @Test fun authenticCoreRoutesSelectedUidAndRetainsBlockingTunOnReconnect() = runBlocking {
        assertNull("Grant VPN permission using appops before testing", VpnService.prepare(context))
        val store = SecretStore(context)
        assertNotNull("Load the encrypted synthetic fixture before running", store.get("synthetic-profile"))
        store.put("token", "synthetic-test-token")
        val start = Intent(context, VeilwayVpnService::class.java).setAction("connect")
            .putExtra("profile", "00000000-0000-4000-8000-000000000001")
            .putExtra("apps", arrayOf("com.android.shell")).putExtra("synthetic-test", true)
        try {
            context.startForegroundService(start)
            awaitState { it == "Подключён" }
            assertTrue(shell("ping -c 1 -W 3 10.243.99.1").contains("1 received"))
            assertTrue(shell("ping6 -c 1 -W 3 fd12:feed:99::1").contains("1 received"))
            assertTrue(shell("ping -c 1 -W 3 fixture.veilway.test").contains("1 received"))
            shell("input keyevent KEYCODE_SLEEP")
            Thread.sleep(1000)
            assertTrue(shell("ping -c 1 -W 3 10.243.99.1").contains("1 received"))
            shell("input keyevent KEYCODE_WAKEUP")
            control("direct")
            control("stop")
            awaitState { it.startsWith("Восстанавливается") }
            assertFalse(shell("ping -c 1 -W 2 10.243.99.1").contains("1 received"))
            assertFalse(shell("ping6 -c 1 -W 2 fd12:feed:99::1").contains("1 received"))
            assertFalse(shell("ping -c 1 -W 2 10.0.2.2").contains("1 received"))
            control("direct")
            control("start")
            awaitState { it == "Подключён" }
            assertTrue(shell("ping -c 1 -W 3 10.243.99.1").contains("1 received"))
        } finally {
            context.startService(Intent(context, VeilwayVpnService::class.java).setAction("disconnect"))
        }
        awaitState { it == "Отключён" }
        assertTrue(shell("ping -c 1 -W 3 10.0.2.2").contains("1 received"))
        store.clear()
    }
}
