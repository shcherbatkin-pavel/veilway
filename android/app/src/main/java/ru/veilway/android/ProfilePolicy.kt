package ru.veilway.android

/** Accept only self-contained Veilway profiles; never execute external scripts. */
object ProfilePolicy {
    private val blocks = setOf("ca", "cert", "key", "tls-crypt-v2")
    private val directives = setOf("client", "dev", "proto", "remote", "nobind", "persist-key", "persist-tun",
        "remote-cert-tls", "verify-x509-name", "tls-version-min", "tls-cert-profile", "data-ciphers", "auth",
        "allow-compression", "block-ipv6", "verb")
    fun validate(profile: String) {
        require(profile.length <= 1048576 && !profile.contains('\u0000')) { "Некорректный профиль" }
        var block: String? = null
        val seenBlocks = mutableSetOf<String>()
        val options = mutableMapOf<String, String>()
        profile.lineSequence().forEach { raw ->
            val line = raw.trim()
            if (block != null) {
                if (line == "</$block>") block = null
            } else if (line.isNotEmpty() && !line.startsWith("#") && !line.startsWith(";")) {
                if (line.startsWith("<")) {
                    val name = line.removePrefix("<").removeSuffix(">")
                    require(line == "<$name>" && name in blocks && seenBlocks.add(name)) { "Неподдерживаемый блок профиля" }
                    block = name
                } else {
                    val name = line.substringBefore(' ')
                    require(name in directives && !options.containsKey(name)) { "Неподдерживаемые настройки профиля" }
                    options[name] = line.substringAfter(' ', "").trim()
                }
            }
        }
        require(block == null && seenBlocks == blocks) { "Профиль должен содержать сертификаты и ключи" }
        require(options["dev"] == "tun" && options["proto"] == "udp4" && options.containsKey("client")) { "Неподдерживаемый тип подключения" }
        require(options["remote-cert-tls"] == "server" && options["verify-x509-name"]?.endsWith(" name") == true
            && options["tls-version-min"] == "1.3" && options["allow-compression"] == "no") { "Профиль не соответствует требованиям защиты Veilway" }
        require(options.containsKey("remote") && options["data-ciphers"]?.split(':')?.all {
            it in setOf("CHACHA20-POLY1305", "AES-256-GCM", "AES-128-GCM")
        } == true) { "Неподдерживаемые шифры" }
    }
}
