package com.nuovogoat.doctorcode.data

import java.net.Inet6Address
import java.net.InetAddress
import java.net.URI
import java.net.UnknownHostException

/**
 * The companion only talks to a desktop on the private LAN.
 *
 * The desktop bridge is cleartext HTTP/WebSocket by design.  Restricting hosts
 * before the first request keeps a mistyped or malicious address from turning
 * the phone into a public HTTP client.
 */
object NetworkPolicy {
    private val ipv4 = Regex("""^\d{1,3}(?:\.\d{1,3}){3}$""")

    fun normalizeBaseUrl(raw: String): String {
        val trimmed = raw.trim()
        require(trimmed.isNotEmpty()) { "address is empty" }
        val withScheme = if ("://" in trimmed) trimmed else "http://$trimmed"
        val uri = URI(withScheme)
        val scheme = uri.scheme?.lowercase()
        require(scheme == "http" || scheme == "https") { "unsupported scheme" }
        require(uri.rawUserInfo == null) { "credentials are not allowed in the address" }
        require(uri.rawQuery == null && uri.rawFragment == null) { "query is not allowed" }
        require(uri.rawPath.isNullOrEmpty() || uri.rawPath == "/") { "use host and port only" }
        val host = uri.host ?: throw IllegalArgumentException("address has no host")
        val port = if (uri.port == -1) "" else ":${uri.port}"
        return "$scheme://$host$port"
    }

    fun isAllowed(baseUrl: String): Boolean {
        val uri = runCatching { URI(baseUrl) }.getOrNull() ?: return false
        val scheme = uri.scheme?.lowercase()
        if (scheme != "http" && scheme != "https") return false
        val host = uri.host?.removeSurrounding("[", "]") ?: return false
        return isPrivateHost(host)
    }

    fun isPrivateHost(host: String): Boolean {
        val normalized = host.trim().trimEnd('.').lowercase()
        if (normalized == "localhost" || normalized.endsWith(".local")) return true

        if (ipv4.matches(normalized)) {
            val parts = normalized.split('.').mapNotNull { it.toIntOrNull() }
            if (parts.size != 4 || parts.any { it !in 0..255 }) return false
            return isPrivateIpv4(parts[0], parts[1])
        }

        if (':' in normalized) {
            return try {
                val address = InetAddress.getByName(normalized)
                if (isIpv4Mapped(address)) {
                    val bytes = address.address
                    isPrivateIpv4(bytes[12].toInt() and 0xFF, bytes[13].toInt() and 0xFF)
                } else {
                    address.isLoopbackAddress ||
                        address.isLinkLocalAddress ||
                        address.isSiteLocalAddress ||
                        isUniqueLocalIpv6(address)
                }
            } catch (_: UnknownHostException) {
                false
            }
        }
        return false
    }

    private fun isPrivateIpv4(first: Int, second: Int): Boolean =
        first == 10 ||
            first == 127 ||
            (first == 192 && second == 168) ||
            (first == 172 && second in 16..31) ||
            (first == 169 && second == 254) ||
            (first == 100 && second in 64..127)

    private fun isIpv4Mapped(address: InetAddress): Boolean {
        if (address !is Inet6Address) return false
        val bytes = address.address
        if (bytes.size != 16) return false
        for (index in 0..9) {
            if (bytes[index] != 0.toByte()) return false
        }
        return bytes[10] == 0xFF.toByte() && bytes[11] == 0xFF.toByte()
    }

    private fun isUniqueLocalIpv6(address: InetAddress): Boolean {
        if (address !is Inet6Address) return false
        val bytes = address.address
        return bytes.size == 16 && (bytes[0].toInt() and 0xFE) == 0xFC
    }
}
