package ai.oyi.device

import android.app.Application

class OyiApplication : Application() {

    lateinit var container: AppContainer
        private set

    override fun onCreate() {
        super.onCreate()
        container = AppContainer(this)
        // Blocking must not depend on the overlay service being switched on.
        container.focusGuard.start()
    }
}
