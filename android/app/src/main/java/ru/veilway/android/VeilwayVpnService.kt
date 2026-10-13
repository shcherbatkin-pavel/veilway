package ru.veilway.android

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Intent
import android.net.ConnectivityManager
import android.net.Network
import android.net.VpnService
import android.os.Handler
import android.os.Looper
import android.os.ParcelFileDescriptor
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.flow.MutableStateFlow
import java.util.concurrent.atomic.AtomicBoolean

data class TunnelState(val active: Boolean = false, val text: String = "Отключён")
object TunnelStatus { val state = MutableStateFlow(TunnelState()) }

class VeilwayVpnService : VpnService() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val main = Handler(Looper.getMainLooper())
    private val stopping = AtomicBoolean(false)
    private var started = false
    @Volatile private var handle = 0L
    private var core: NativeCore? = null
    private var tun: ParcelFileDescriptor? = null
    private var builder: Builder? = null
    private var apps = emptyList<String>()
    private lateinit var connectivity: ConnectivityManager
    private var networkCallback: ConnectivityManager.NetworkCallback? = null

    override fun onCreate() {
        super.onCreate()
        connectivity = getSystemService(ConnectivityManager::class.java)
        getSystemService(NotificationManager::class.java).createNotificationChannel(
            NotificationChannel("vpn", "VPN-подключение", NotificationManager.IMPORTANCE_LOW))
    }
    private fun notification(text: String): Notification {
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val stop = PendingIntent.getService(this, 1, Intent(this, javaClass).setAction("disconnect"), PendingIntent.FLAG_IMMUTABLE)
        return Notification.Builder(this, "vpn").setSmallIcon(android.R.drawable.ic_lock_lock)
            .setContentTitle("Veilway").setContentText(text).setContentIntent(open).setOngoing(true)
            .addAction(Notification.Action.Builder(null, "Отключить", stop).build()).build()
    }
    private fun state(text: String) {
        if (stopping.get()) return
        TunnelStatus.state.value = TunnelState(true, text)
        main.post { if (!stopping.get()) getSystemService(NotificationManager::class.java).notify(1, notification(text)) }
    }
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == "disconnect") { disconnect(); return START_NOT_STICKY }
        if (started || intent?.action != "connect") return START_NOT_STICKY
        started = true
        val profileId = intent.getStringExtra("profile") ?: run { disconnect(); return START_NOT_STICKY }
        val requested = intent.getStringArrayExtra("apps")?.toList()?.distinct().orEmpty()
        startForeground(1, notification("Подключается"))
        state("Подключается")
        scope.launch {
            try {
                apps = requested.filter { it != packageName && runCatching {
                    packageManager.getApplicationInfo(it, 0); true
                }.getOrDefault(false) }
                require(apps.isNotEmpty()) { "Выберите установленные приложения для VPN" }
                check(prepare(this@VeilwayVpnService) == null) { "Разрешите VPN-подключение" }
                // Install a blocking interface before requesting sensitive material.
                // Retain an owned FD even when the core closes its duplicate.
                synchronized(this@VeilwayVpnService) {
                    check(!stopping.get())
                    tun = appBuilder().addAddress("192.0.2.1", 32).addDnsServer("192.0.2.53").establish()
                    check(tun != null) { "Не удалось создать VPN-интерфейс" }
                }
                val store = SecretStore(this@VeilwayVpnService)
                val token = store.get("token") ?: throw ApiFailure(401)
                val api = MobileApi()
                val material = if (BuildConfig.DEBUG && intent.getBooleanExtra("synthetic-test", false)) {
                    store.get("synthetic-profile") ?: error("Synthetic fixture missing")
                } else {
                    val profile = api.profiles(token).firstOrNull { it.id == profileId && it.usable }
                        ?: throw IllegalArgumentException("Профиль недоступен или срок действия истёк")
                    api.download(token, profile.id)
                }
                ProfilePolicy.validate(material)
                check(!stopping.get() && store.putAuthenticatedProfile("profile-" + profileId, material, token)) { "Сессия завершена" }
                synchronized(this@VeilwayVpnService) {
                    check(!stopping.get())
                    core = NativeCore()
                    handle = core!!.create(this@VeilwayVpnService)
                }
                val callback = object : ConnectivityManager.NetworkCallback() {
                    override fun onAvailable(network: Network) { if (handle != 0L && !stopping.get()) core?.reconnect(handle) }
                    override fun onLost(network: Network) { if (!stopping.get()) { state("Восстанавливается — выбранные приложения ждут VPN"); core?.reconnect(handle) } }
                }
                synchronized(this@VeilwayVpnService) {
                    check(!stopping.get())
                    networkCallback = callback
                    connectivity.registerDefaultNetworkCallback(callback)
                }
                val ownedHandle = handle
                try { core!!.run(ownedHandle, material) }
                finally { core!!.destroy(ownedHandle); handle = 0L }
                if (!stopping.get()) state("Соединение остановлено — выбранные приложения заблокированы. Отключите VPN или повторите подключение.")
            } catch (error: Exception) {
                if (!stopping.get()) {
                    val text = when (error) {
                        is ApiFailure -> if (error.status == 401) "Войдите в аккаунт заново" else "Панель недоступна или профиль нельзя скачать"
                        is IllegalArgumentException -> error.message ?: "Неподдерживаемый профиль"
                        else -> "Не удалось подключиться"
                    }
                    state("$text. Нажмите «Отключить», чтобы снять блокировку.")
                }
            }
        }
        return START_NOT_STICKY
    }
    private fun appBuilder(): Builder {
        check(apps.isNotEmpty())
        return Builder().setSession("Veilway").setBlocking(false).setMtu(1500)
            .addRoute("0.0.0.0", 0).addRoute("::", 0).apply { apps.forEach { addAllowedApplication(it) } }
    }
    // JNI callbacks are invoked only on the OpenVPN worker thread.
    fun protectSocket(fd: Int): Boolean = !stopping.get() && protect(fd)
    fun newBuilder(): Boolean { if (stopping.get()) return false; builder = appBuilder(); return true }
    fun addAddress(address: String, prefix: Int): Boolean { builder!!.addAddress(address, prefix); return true }
    fun addRoute(address: String, prefix: Int): Boolean { builder!!.addRoute(address, prefix); return true }
    fun addDns(address: String): Boolean { builder!!.addDnsServer(address); return true }
    fun setMtu(mtu: Int): Boolean { builder!!.setMtu(mtu); return true }
    @Synchronized fun establish(): Int {
        if (stopping.get()) return -1
        val replacement = builder!!.establish() ?: return -1
        // Establish the replacement before closing the old descriptor.
        val previous = tun
        tun = replacement
        previous?.close()
        return ParcelFileDescriptor.dup(replacement.fileDescriptor).detachFd()
    }
    fun nativeEvent(name: String): Boolean {
        when (name) {
            "CONNECTED" -> state("Подключён")
            "RECONNECTING", "WAIT", "RESOLVE", "CONNECTING", "PAUSE" -> state("Восстанавливается — выбранные приложения ждут VPN")
            "PROFILE_REJECTED" -> state("Неподдерживаемый профиль — выбранные приложения заблокированы")
            "AUTH_FAILED", "CERT_VERIFY_FAIL", "TLS_VERSION_MIN", "TUNNEL_FAILED" -> state("Ошибка VPN — выбранные приложения заблокированы")
        }
        return true
    }
    @Synchronized private fun disconnect() {
        if (!stopping.compareAndSet(false, true)) return
        core?.stop(handle)
        networkCallback?.let { runCatching { connectivity.unregisterNetworkCallback(it) } }
        scope.cancel()
        tun?.close(); tun = null
        TunnelStatus.state.value = TunnelState()
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }
    override fun onRevoke() { disconnect(); super.onRevoke() }
    override fun onDestroy() { disconnect(); super.onDestroy() }
}
