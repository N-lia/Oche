package ai.oyi.device.data

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import ai.oyi.device.BuildConfig
import ai.oyi.device.model.DeviceSettings
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.util.UUID

/**
 * Stores the gateway configuration.
 *
 * The pairing token is kept in an EncryptedSharedPreferences file so it is not
 * readable from a plain backup or an unlocked file system dump.
 */
class SettingsStore(context: Context) {

    private val appContext = context.applicationContext

    private val masterKey: MasterKey = MasterKey.Builder(appContext)
        .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
        .build()

    private val securePrefs: SharedPreferences = EncryptedSharedPreferences.create(
        appContext,
        "oyi_secure",
        masterKey,
        EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
        EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
    )

    private val prefs: SharedPreferences =
        appContext.getSharedPreferences("oyi_settings", Context.MODE_PRIVATE)

    private val _settings = MutableStateFlow(read())
    val settings: StateFlow<DeviceSettings> = _settings.asStateFlow()

    init {
        if (securePrefs.getString(KEY_DEVICE_ID, null).isNullOrBlank()) {
            securePrefs.edit()
                .putString(KEY_DEVICE_ID, UUID.randomUUID().toString())
                .apply()
        }
        refresh()
    }

    fun saveGateway(url: String, token: String) {
        securePrefs.edit()
            .putString(KEY_GATEWAY_URL, url.trim())
            .putString(KEY_TOKEN, token.trim())
            .apply()
        refresh()
    }

    fun setOverlayEnabled(enabled: Boolean) {
        prefs.edit().putBoolean(KEY_OVERLAY_ENABLED, enabled).apply()
        refresh()
    }

    fun setAutoStart(enabled: Boolean) {
        prefs.edit().putBoolean(KEY_AUTO_START, enabled).apply()
        refresh()
    }

    fun refresh() {
        _settings.value = read()
    }

    private fun read(): DeviceSettings {
        val gatewayUrl = securePrefs.getString(KEY_GATEWAY_URL, null)
            ?.takeIf { it.isNotBlank() }
            ?: BuildConfig.DEFAULT_GATEWAY_URL
        return DeviceSettings(
            gatewayUrl = gatewayUrl.orEmpty(),
            token = securePrefs.getString(KEY_TOKEN, "").orEmpty(),
            overlayEnabled = prefs.getBoolean(KEY_OVERLAY_ENABLED, false),
            autoStart = prefs.getBoolean(KEY_AUTO_START, false),
            deviceId = securePrefs.getString(KEY_DEVICE_ID, "").orEmpty(),
        )
    }

    private companion object {
        const val KEY_GATEWAY_URL = "gateway_url"
        const val KEY_TOKEN = "device_token"
        const val KEY_DEVICE_ID = "device_id"
        const val KEY_OVERLAY_ENABLED = "overlay_enabled"
        const val KEY_AUTO_START = "auto_start"
    }
}
