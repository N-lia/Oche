package ai.oyi.device

import android.content.Context
import ai.oyi.device.data.SettingsStore
import ai.oyi.device.data.UsageRepository
import ai.oyi.device.focus.FocusGuard
import ai.oyi.device.focus.FocusStore
import ai.oyi.device.focus.TrustedClock
import ai.oyi.device.net.DeviceClient
import ai.oyi.device.runtime.DeviceRuntime

/** Small manual dependency container (no DI framework needed for v1). */
class AppContainer(context: Context) {

    val settings: SettingsStore = SettingsStore(context)

    val usage: UsageRepository = UsageRepository(context)

    val runtime: DeviceRuntime = DeviceRuntime(context)

    val deviceClient: DeviceClient = DeviceClient(context, settings, runtime, usage)

    /** The blocker: rules, the lock's clock, and the enforcement loop. */
    val clock: TrustedClock = TrustedClock(context)

    val focusStore: FocusStore = FocusStore(context, clock)

    val focusGuard: FocusGuard = FocusGuard(context, focusStore, clock, runtime, settings)
}
