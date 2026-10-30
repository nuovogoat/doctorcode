package com.nuovogoat.doctorcode.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class NetworkPolicyTest {
    @Test
    fun `bare host and port gain the http scheme`() {
        assertEquals("http://192.168.1.20:8000", NetworkPolicy.normalizeBaseUrl("192.168.1.20:8000"))
        assertEquals("http://desktop.local:8000", NetworkPolicy.normalizeBaseUrl("desktop.local:8000"))
    }

    @Test
    fun `trailing slash is dropped and the scheme is lowercased`() {
        assertEquals("http://desktop.local:8000", NetworkPolicy.normalizeBaseUrl("HTTP://desktop.local:8000/"))
    }

    @Test(expected = IllegalArgumentException::class)
    fun `credentials in the address are rejected`() {
        NetworkPolicy.normalizeBaseUrl("http://user:secret@192.168.1.20:8000")
    }

    @Test(expected = IllegalArgumentException::class)
    fun `query strings are rejected`() {
        NetworkPolicy.normalizeBaseUrl("http://192.168.1.20:8000/?token=secret")
    }

    @Test(expected = IllegalArgumentException::class)
    fun `path prefixes are rejected`() {
        NetworkPolicy.normalizeBaseUrl("http://192.168.1.20:8000/api")
    }

    @Test(expected = IllegalArgumentException::class)
    fun `non-http schemes are rejected`() {
        NetworkPolicy.normalizeBaseUrl("ftp://192.168.1.20:8000")
    }

    @Test
    fun `private ipv4 ranges are allowed`() {
        assertTrue(NetworkPolicy.isAllowed("http://192.168.1.20:8000"))
        assertTrue(NetworkPolicy.isAllowed("http://10.0.0.5:8000"))
        assertTrue(NetworkPolicy.isAllowed("http://172.16.0.1:8000"))
        assertTrue(NetworkPolicy.isAllowed("http://172.31.255.254:8000"))
        assertTrue(NetworkPolicy.isAllowed("http://127.0.0.1:8000"))
        assertTrue(NetworkPolicy.isAllowed("http://169.254.4.1:8000"))
        assertTrue(NetworkPolicy.isAllowed("http://100.64.0.1:8000"))
        assertTrue(NetworkPolicy.isAllowed("http://100.127.255.254:8000"))
    }

    @Test
    fun `public and unsupported hosts are rejected`() {
        assertFalse(NetworkPolicy.isAllowed("http://8.8.8.8:8000"))
        assertFalse(NetworkPolicy.isAllowed("http://172.32.0.1:8000"))
        assertFalse(NetworkPolicy.isAllowed("http://100.128.0.1:8000"))
        assertFalse(NetworkPolicy.isAllowed("http://example.com:8000"))
        assertFalse(NetworkPolicy.isAllowed("ftp://192.168.1.20:8000"))
    }

    @Test
    fun `mdns and loopback names are allowed`() {
        assertTrue(NetworkPolicy.isAllowed("http://localhost:8000"))
        assertTrue(NetworkPolicy.isAllowed("http://desktop.local:8000"))
        assertTrue(NetworkPolicy.isAllowed("http://DESKTOP.LOCAL.:8000"))
    }

    @Test
    fun `private ipv6 addresses are allowed`() {
        assertTrue(NetworkPolicy.isAllowed("https://[::1]:8443"))
        assertTrue(NetworkPolicy.isAllowed("http://[fd00::1]:8000"))
        assertTrue(NetworkPolicy.isAllowed("http://[fe80::1]:8000"))
    }

    @Test
    fun `global ipv6 addresses are rejected`() {
        assertFalse(NetworkPolicy.isAllowed("http://[2001:4860:4860::8888]:8000"))
    }

    @Test
    fun `out of range octets are rejected`() {
        assertFalse(NetworkPolicy.isAllowed("http://192.168.300.1:8000"))
        assertFalse(NetworkPolicy.isAllowed("http://999.999.999.999:8000"))
    }
}
