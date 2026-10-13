package ru.veilway.android

class NativeCore {
    companion object { init { System.loadLibrary("veilway_openvpn") } }
    external fun create(service: VeilwayVpnService): Long
    external fun run(handle: Long, profile: String)
    external fun stop(handle: Long)
    external fun reconnect(handle: Long)
    external fun destroy(handle: Long)
}
