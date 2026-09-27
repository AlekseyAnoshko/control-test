package ru.aaf.controltest

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.Handler
import android.os.Looper

class ExamNetworkGuard(
    context: Context,
    private val onStatusChanged: (Status) -> Unit
) {
    enum class Status {
        VPN_ACTIVE,
        VPN_NOT_DETECTED,
        NETWORK_UNKNOWN
    }

    private val connectivityManager =
        context.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager

    private val mainHandler = Handler(Looper.getMainLooper())
    private var started = false

    private val callback = object : ConnectivityManager.NetworkCallback() {
        override fun onAvailable(network: android.net.Network) {
            publishCurrentStatus()
        }

        override fun onLost(network: android.net.Network) {
            publishCurrentStatus()
        }

        override fun onCapabilitiesChanged(
            network: android.net.Network,
            capabilities: NetworkCapabilities
        ) {
            publishCurrentStatus()
        }
    }

    fun currentStatus(): Status {
        val network = connectivityManager.activeNetwork ?: return Status.NETWORK_UNKNOWN
        val capabilities = connectivityManager.getNetworkCapabilities(network)
            ?: return Status.NETWORK_UNKNOWN

        return if (capabilities.hasTransport(NetworkCapabilities.TRANSPORT_VPN)) {
            Status.VPN_ACTIVE
        } else {
            Status.VPN_NOT_DETECTED
        }
    }

    fun start() {
        if (started) return
        connectivityManager.registerDefaultNetworkCallback(callback)
        started = true
        publishCurrentStatus()
    }

    fun stop() {
        if (!started) return
        connectivityManager.unregisterNetworkCallback(callback)
        started = false
    }

    private fun publishCurrentStatus() {
        mainHandler.post {
            if (started) onStatusChanged(currentStatus())
        }
    }
}
