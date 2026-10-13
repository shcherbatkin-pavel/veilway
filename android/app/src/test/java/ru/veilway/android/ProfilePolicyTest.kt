package ru.veilway.android

import org.junit.Assert.assertThrows
import org.junit.Test

class ProfilePolicyTest {
    private fun profile(extra: String = "") = """
        client
        dev tun
        proto udp4
        remote vpn.example.test 1195
        remote-cert-tls server
        verify-x509-name synthetic-server name
        tls-version-min 1.3
        allow-compression no
        data-ciphers CHACHA20-POLY1305:AES-256-GCM:AES-128-GCM
        $extra
        <ca>
        synthetic-test-data
        </ca>
        <cert>
        synthetic-test-data
        </cert>
        <key>
        synthetic-test-data
        </key>
        <tls-crypt-v2>
        synthetic-test-data
        </tls-crypt-v2>
    """.trimIndent()
    @Test fun acceptsVeilwayOptionsWithoutEditingMaterial() { ProfilePolicy.validate(profile("block-ipv6")) }
    @Test fun rejectsScriptAndExternalConfigDirectives() {
        for (directive in listOf("up /tmp/script", "config /tmp/other", "auth-user-pass", "route-nopull", "redirect-gateway def1")) {
            assertThrows(IllegalArgumentException::class.java) { ProfilePolicy.validate(profile(directive)) }
        }
    }
    @Test fun rejectsMissingServerVerificationAndWeakerTls() {
        for (content in listOf(profile().replace("remote-cert-tls server", ""), profile().replace("tls-version-min 1.3", "tls-version-min 1.2"),
            profile().replace("allow-compression no", "allow-compression yes"), profile().replace("</key>", ""))) {
            assertThrows(IllegalArgumentException::class.java) { ProfilePolicy.validate(content) }
        }
    }
}
