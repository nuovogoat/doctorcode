package com.nuovogoat.doctorcode.data

import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import java.util.ArrayDeque
import java.util.concurrent.atomic.AtomicBoolean

data class DiscoveredServer(
    val name: String,
    val host: String,
    val port: Int,
    val pairingRequired: Boolean,
    val version: String,
) {
    val address: String
        get() = "$host:$port"
}

/**
 * Android NSD wrapper for the desktop's ``_doctorcode._tcp.`` advertisement.
 *
 * Only the endpoint, version, path, and pairing flag are published by the
 * desktop.  Resolution is serialized because Android NSD permits one active
 * resolve request at a time.
 */
class ServerDiscovery(context: Context) {
    private val nsd = context.applicationContext
        .getSystemService(Context.NSD_SERVICE) as? NsdManager

    private val queue = ArrayDeque<NsdServiceInfo>()
    private val resolving = AtomicBoolean(false)
    private var discoveryListener: NsdManager.DiscoveryListener? = null
    private var onFound: ((DiscoveredServer) -> Unit)? = null
    private var onLost: ((String) -> Unit)? = null
    private var onError: ((String) -> Unit)? = null

    @Volatile
    private var started = false

    fun start(
        onFound: (DiscoveredServer) -> Unit,
        onLost: (String) -> Unit,
        onError: (String) -> Unit,
    ) {
        if (started) return
        val manager = nsd
        if (manager == null) {
            onError("Android NSD is unavailable")
            return
        }
        this.onFound = onFound
        this.onLost = onLost
        this.onError = onError

        val listener = object : NsdManager.DiscoveryListener {
            override fun onDiscoveryStarted(serviceType: String) {
                started = true
            }

            override fun onServiceFound(serviceInfo: NsdServiceInfo) {
                if (!started) return
                if (serviceInfo.serviceType != SERVICE_TYPE) return
                synchronized(queue) { queue.addLast(serviceInfo) }
                resolveNext(manager)
            }

            override fun onServiceLost(serviceInfo: NsdServiceInfo) {
                if (!started) return
                onLost?.invoke(serviceInfo.serviceName ?: "")
            }

            override fun onDiscoveryStopped(serviceType: String) {
                started = false
            }

            override fun onStartDiscoveryFailed(serviceType: String, errorCode: Int) {
                started = false
                onError?.invoke("Discovery failed (code $errorCode)")
            }

            override fun onStopDiscoveryFailed(serviceType: String, errorCode: Int) {
                // Stopping is best-effort; a later start repairs the listener.
            }
        }
        discoveryListener = listener
        try {
            manager.discoverServices(SERVICE_TYPE, NsdManager.PROTOCOL_DNS_SD, listener)
        } catch (exc: Exception) {
            started = false
            discoveryListener = null
            onError?.invoke(exc.message ?: "Discovery failed")
        }
    }

    fun stop() {
        started = false
        queue.clear()
        resolving.set(false)
        val manager = nsd ?: return
        val listener = discoveryListener ?: return
        discoveryListener = null
        try {
            manager.stopServiceDiscovery(listener)
        } catch (_: Exception) {
            // The listener may have already been stopped by the system.
        }
    }

    @Suppress("DEPRECATION")
    private fun resolveNext(manager: NsdManager) {
        if (!started) return
        if (!resolving.compareAndSet(false, true)) return
        val info = synchronized(queue) { queue.pollFirst() }
        if (info == null) {
            resolving.set(false)
            return
        }
        try {
            manager.resolveService(
                info,
                object : NsdManager.ResolveListener {
                    override fun onResolveFailed(serviceInfo: NsdServiceInfo, errorCode: Int) {
                        resolving.set(false)
                        resolveNext(manager)
                    }

                    override fun onServiceResolved(serviceInfo: NsdServiceInfo) {
                        if (started) {
                            val host = serviceInfo.host?.hostAddress.orEmpty()
                            if (host.isNotBlank() && serviceInfo.port > 0) {
                                val attributes = serviceInfo.attributes.orEmpty()
                                onFound?.invoke(
                                    DiscoveredServer(
                                        name = serviceInfo.serviceName ?: "DoctorCode",
                                        host = host,
                                        port = serviceInfo.port,
                                        pairingRequired = attributes["pairing"]
                                            ?.toString(Charsets.UTF_8) != "disabled",
                                        version = attributes["version"]
                                            ?.toString(Charsets.UTF_8)
                                            .orEmpty(),
                                    ),
                                )
                            }
                        }
                        resolving.set(false)
                        resolveNext(manager)
                    }
                },
            )
        } catch (_: Exception) {
            resolving.set(false)
            resolveNext(manager)
        }
    }

    companion object {
        const val SERVICE_TYPE = "_doctorcode._tcp."
    }
}
