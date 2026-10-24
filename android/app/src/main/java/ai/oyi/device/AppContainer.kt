package ai.oyi.device

import android.content.Context
import ai.oyi.device.data.SettingsStore
import ai.oyi.device.data.UsageRepository
import ai.oyi.device.net.DeviceClient
import ai.oyi.device.runtime.DeviceRuntime

/** Small manual dependency container (no DI framework needed for v1). */
class AppContainer(context: Context) {

    val settings: SettingsStore = SettingsStore(context)

    val usage: UsageRepository = UsageRepository(context)

    val runtime: DeviceRuntime = DeviceRuntime(context)

    val deviceClient: DeviceClient = DeviceClient(context, settings, runtime, usage)
}
